"""CourseSession: thin orchestrator gluing audio bridge, visual stream,
note fusion loop and storage. No new capture/inference machinery.

Round 3: unified cleanup core path — stop/fatal/exception all funnel through
the same stop_workers + release_models + verify_ps sequence.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from .events import TranscriptEvent, VisualEvent
from .note_engine import NoteEngine
from .settings import (
    FUSION_INTERVAL_S,
    WORKER_JOIN_TIMEOUT,
    CleanupStatus,
    mark_model_loaded,
)
from .storage import SessionStorage
from .audio_bridge import AudioBridge
from .visual import VisualStream

# Region comes from QLens
from core.capture import Region  # noqa: E402 (upstream reuse)

log = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class StopResult:
    """Honest stop outcome for GUI / scripts (never claims success on failure)."""

    success: bool
    state: str
    summary_path: Optional[str] = None
    error: Optional[str] = None
    partial_available: bool = False
    workers_stopped: bool = True
    lingering_workers: Optional[list] = None


class CourseSession:
    def __init__(
        self,
        region: Region,
        on_status: Optional[Callable[[str], None]] = None,
        on_stop_requested: Optional[Callable[[], None]] = None,
    ) -> None:
        self.region = region
        self._on_status = on_status or (lambda s: None)
        self._on_stop_requested = on_stop_requested

        self.storage = SessionStorage()
        self.note_engine = NoteEngine(self.storage)

        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self._transcripts: List[TranscriptEvent] = []
        self._visuals: List[VisualEvent] = []
        self._unfused_transcripts: List[TranscriptEvent] = []
        self._unfused_visuals: List[VisualEvent] = []

        self._stop_fusion = threading.Event()
        self._fusion_thread: Optional[threading.Thread] = None
        self._flag_thread: Optional[threading.Thread] = None
        self._finalizing = False
        self.running = False
        self.state = SessionState.IDLE
        self._stop_result: Optional[StopResult] = None
        self._audio_failed = False

        self.audio: Optional[AudioBridge] = None
        self.visual: Optional[VisualStream] = None
        self.last_error: str = ""

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start audio + visual + fusion. Blocks until whisper is loaded.

        Round 3: preflight_cleanup() runs BEFORE any workers start to unload
        leftover FINAL_MODEL from a previous session.
        """
        # Preflight: unload leftover FINAL_MODEL before starting realtime
        from .final_summary import preflight_cleanup

        pf_ok, pf_msg = preflight_cleanup()
        if not pf_ok:
            self.last_error = f"preflight cleanup failed: {pf_msg}"
            self.state = SessionState.FAILED
            raise RuntimeError(
                f"Cannot start session: {pf_msg}. "
                "Previous final model must be unloaded before starting realtime phase."
            )

        self.running = True
        self.state = SessionState.RUNNING
        self.storage.ensure_live_header(
            f"课程笔记 — {time.strftime('%Y-%m-%d %H:%M')}"
        )
        self.storage.write_active_marker()

        # Audio (Hearsay assembly). Blocking (model load).
        try:
            self.audio = AudioBridge(
                on_event=self._on_transcript,
                transcript_dir=self.storage.dir,
                t0=self._t0,
                on_fatal=self._on_audio_fatal,
            )
            self._status("启动音频与 Whisper…")
            self.audio.start()
            self.storage.set_transcript_writer_path(self.audio.writer_path)
        except Exception as e:
            # reverse partial start of visual not yet begun; audio rolls back itself
            self.last_error = f"audio start failed: {e}"
            self.running = False
            self.state = SessionState.FAILED
            self.storage.clear_active_marker()
            try:
                if self.audio:
                    self.audio.stop()
            except Exception:
                pass
            raise

        # Visual (QLens capture + VLM). Non-blocking thread.
        try:
            self.visual = VisualStream(
                region=self.region,
                on_event=self._on_visual,
                t0=self._t0,
            )
            self._status("启动视觉流…")
            self.visual.start()
        except Exception as e:
            self.last_error = f"visual start failed: {e}"
            self.running = False
            self.state = SessionState.FAILED
            try:
                if self.audio:
                    self.audio.stop()
            except Exception:
                pass
            self.storage.clear_active_marker()
            raise

        self._stop_fusion.clear()
        self._fusion_thread = threading.Thread(
            target=self._fusion_loop, name="FusionLoop", daemon=True
        )
        self._fusion_thread.start()
        self._flag_thread = threading.Thread(
            target=self._flag_loop, name="StopFlagWatch", daemon=True
        )
        self._flag_thread.start()
        self._status(
            f"课程会话已开始：{self.storage.dir.name} "
            f"(VLM={self.visual._model}, whisper={self.audio.last_status})"
        )

    def _status(self, msg: str) -> None:
        log.info(msg)
        try:
            self._on_status(msg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _on_transcript(self, ev: TranscriptEvent) -> None:
        with self._lock:
            self._transcripts.append(ev)
            self._unfused_transcripts.append(ev)
        self.storage.append_event(ev.to_dict())

    def _on_visual(self, ev: VisualEvent) -> None:
        with self._lock:
            self._visuals.append(ev)
            self._unfused_visuals.append(ev)
        self.storage.append_visual_event(ev.to_dict())
        self._status(
            f"VisualEvent #{ev.frame_id} @t={ev.session_t:.0f}s "
            f"(diff={ev.diff_score})"
        )

    def _on_audio_fatal(self, exc: Exception) -> None:
        """Audio fatal: full cleanup via unified path, NO full 27B summary.

        Goes through _cleanup_core: stop visual + audio + fusion, release models,
        verify /api/ps, write partial_notes.md.
        """
        if self._finalizing:
            # stop already in progress; just record the error
            if not self.last_error:
                self.last_error = f"audio fatal: {exc}"
            return
        self.last_error = f"audio fatal: {exc}"
        self._audio_failed = True
        self.state = SessionState.FAILED
        self._status(f"音频致命错误: {exc} — 将停止会话并写入 partial_notes")

        def _run() -> None:
            try:
                result = self._cleanup_core(
                    stop_visual=True,
                    stop_audio=True,
                    stop_fusion=True,
                    release_models=True,
                    wait_final_summary=False,
                    failed=True,
                )
                self._status(
                    f"音频失败后的停止完成: success={result.success} "
                    f"partial={result.partial_available} err={result.error}"
                )
            except Exception:
                log.error("stop after audio fatal failed\n%s", traceback.format_exc())

        threading.Thread(target=_run, daemon=True, name="AudioFatalStop").start()

    def _flag_loop(self) -> None:
        """Watch sessions/<this>/STOP_REQUEST written by stop_course.ps1."""
        flag = self.storage.dir / "STOP_REQUEST"
        while not self._stop_fusion.is_set():
            if flag.exists() and not self._finalizing:
                self._status("检测到 STOP_REQUEST")
                if self._on_stop_requested:
                    try:
                        self._on_stop_requested()
                    except Exception:
                        log.error("on_stop_requested failed", exc_info=True)
                return
            self._stop_fusion.wait(2.0)

    # ------------------------------------------------------------------
    def _fusion_loop(self) -> None:
        while not self._stop_fusion.wait(FUSION_INTERVAL_S):
            try:
                self._fuse_once()
            except Exception:
                log.error("fusion loop error\n%s", traceback.format_exc())

    def _fuse_once(self) -> None:
        with self._lock:
            if not self._unfused_transcripts and not self._unfused_visuals:
                return
            tr = self._unfused_transcripts[:]
            vi = self._unfused_visuals[:]
            self._unfused_transcripts.clear()
            self._unfused_visuals.clear()

        updated = self.note_engine.fuse(tr, vi)
        mark_model_loaded(self.note_engine.model)
        st = self.note_engine.state
        self._status(
            f"增量笔记 #{st.updates}: 主题={st.current_topic or '-'} "
            f"概念={len(st.current_concepts)} 公式={len(st.formulas)} "
            f"强调={len(st.teacher_emphasis)}"
            + ("" if updated else "（skip/失败）")
        )

    # ------------------------------------------------------------------
    def _lingering_workers(self) -> list[str]:
        out: list[str] = []
        if self.visual is not None and self.visual.is_alive():
            out.append("visual")
        if self._fusion_thread is not None and self._fusion_thread.is_alive():
            out.append("fusion")
        if self.audio is not None:
            out.extend(f"audio:{n}" for n in self.audio.workers_alive())
        return out

    def stop(
        self,
        wait_final_summary: bool = True,
        failed: bool = False,
    ) -> StopResult:
        """Stop streams, then (optionally) generate final summary.

        Thin wrapper: concurrency handling then unified _cleanup_core path.
        Returns StopResult — success only if workers stopped AND (when
        requested AND not failed) final_summary.md was written.
        Never loads FINAL_MODEL while a worker is still alive (VRAM gate).
        """
        if self._finalizing and self._stop_result is not None:
            return self._stop_result
        if self._finalizing:
            # concurrent stop: wait briefly for the other to publish a result
            deadline = time.monotonic() + WORKER_JOIN_TIMEOUT
            while self._stop_result is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if self._stop_result is not None:
                return self._stop_result

        return self._cleanup_core(
            stop_visual=True,
            stop_audio=True,
            stop_fusion=True,
            release_models=True,
            wait_final_summary=wait_final_summary,
            failed=failed,
        )

    def _cleanup_core(
        self,
        stop_visual: bool = True,
        stop_audio: bool = True,
        stop_fusion: bool = True,
        release_models: bool = True,
        wait_final_summary: bool = True,
        failed: bool = False,
    ) -> StopResult:
        """Unified cleanup path for stop / audio-fatal / exception.

        Sequence (constraint 4 — single path, no three release logics):
          stop workers -> join + liveness verify -> release realtime models
          -> /api/ps verify -> cleanup result (+ optional final summary).
        """
        if self._stop_result is not None:
            return self._stop_result

        self._finalizing = True
        self.state = SessionState.STOPPING
        summary_path: Optional[str] = None
        stop_error: Optional[str] = None
        cleanup_status = CleanupStatus.CLEANUP_OK

        if stop_visual:
            self._status("正在停止视觉流…")
            if self.visual:
                self.visual.stop()
                self.visual.join(timeout=WORKER_JOIN_TIMEOUT)

        if stop_audio:
            self._status("正在停止音频与 Whisper…")
            if self.audio:
                try:
                    audio_cleanup_ok = self.audio.stop()
                    if audio_cleanup_ok is False:
                        stop_error = "audio stop: workers still alive after join"
                except Exception as e:
                    stop_error = f"audio stop failed: {e}"
                    log.error("audio stop failed", exc_info=True)

        if stop_fusion:
            self._stop_fusion.set()
            if self._fusion_thread:
                self._fusion_thread.join(timeout=WORKER_JOIN_TIMEOUT)

            # final fusion for any leftovers
            try:
                self._fuse_once()
            except Exception:
                log.error("final fusion failed", exc_info=True)

        self.storage.finalize_transcript()
        self.running = False

        # VRAM / correctness gate: no 27B load while workers still alive.
        lingering = self._lingering_workers()
        workers_ok = not lingering
        if not workers_ok:
            msg = (
                f"workers still alive after join timeout={WORKER_JOIN_TIMEOUT}s: "
                f"{lingering}; refuse to load {self._note_final_model()}"
            )
            log.error(msg)
            stop_error = (stop_error + "; " if stop_error else "") + msg
            wait_final_summary = False

        # Release tracked realtime models + /api/ps verify (unified path).
        if release_models:
            from .final_summary import release_realtime_models

            self._status("正在释放实时模型并校验 /api/ps…")
            try:
                rel_ok, rel_errs = release_realtime_models()
                if not rel_ok:
                    cleanup_status = CleanupStatus.CLEANUP_FAILED
                    msg = "model release failed: " + ("; ".join(rel_errs) or "unknown")
                    stop_error = (stop_error + "; " if stop_error else "") + msg
                    wait_final_summary = False
                    log.error(msg)
                elif rel_errs:
                    log.warning("model release warnings: %s", "; ".join(rel_errs))
            except Exception as e:
                cleanup_status = CleanupStatus.CLEANUP_FAILED
                stop_error = (stop_error + "; " if stop_error else "") + f"release raised: {e}"
                wait_final_summary = False
                log.error("release_realtime_models raised", exc_info=True)

        # Audio-fatal path: no full summary, but always write partial notes.
        if failed or self._audio_failed or (self.last_error or "").startswith("audio fatal"):
            wait_final_summary = False

        # Cleanup FAILED -> hard gate: never load FINAL_MODEL (constraint 3).
        if cleanup_status != CleanupStatus.CLEANUP_OK:
            wait_final_summary = False

        if wait_final_summary:
            from .final_summary import generate_final_summary

            self._status("正在生成课后总结（qwen38-27b 独占加载）…")
            try:
                summary_path = generate_final_summary(self.storage, self._status)
                self._status(f"完成: {summary_path}")
            except Exception as e:
                stop_error = f"final summary failed: {e}"
                self.last_error = stop_error
                log.error("final summary failed\n%s", traceback.format_exc())
                self._status(f"课后总结失败: {e}")
        else:
            # always leave a partial artifact when full summary is skipped
            try:
                p = self.storage.write_partial_notes(
                    reason=stop_error or self.last_error or "full summary skipped"
                )
                summary_path = str(p)
                self._status(f"partial_notes.md 已写入: {p}")
            except Exception as e:
                log.error("partial notes write failed", exc_info=True)

        self.storage.clear_active_marker()

        if failed or self._audio_failed:
            self.state = SessionState.FAILED
            # Failure path "succeeds" only if workers stopped AND partial
            # notes exist; success still means StopResult.success for GUI.
            # We keep success=False for the user when audio fatal (session failed),
            # but partial_available tells them notes exist.
            success = False
        else:
            success = (
                workers_ok
                and stop_error is None
                and cleanup_status == CleanupStatus.CLEANUP_OK
            )
            if wait_final_summary:
                success = success and summary_path is not None and (
                    self.storage.final_summary_path.exists()
                    or (summary_path and str(summary_path).endswith("final_summary.md"))
                )
            elif summary_path:
                # wait_final_summary=False clean stop: partial is enough
                success = success and Path(summary_path).exists()
            self.state = SessionState.STOPPED if success else SessionState.FAILED

        partial = self.storage.partial_notes_path.exists()
        # For the audio-fatal "failure path completed cleanly" case, surface
        # the original audio error rather than a generic None.
        err_out = stop_error
        if err_out is None and not success:
            err_out = self.last_error or None
        if err_out is None and (failed or self._audio_failed):
            err_out = self.last_error or "audio failed"
        self._stop_result = StopResult(
            success=success,
            state=self.state.value,
            summary_path=summary_path,
            error=err_out,
            partial_available=partial,
            workers_stopped=workers_ok,
            lingering_workers=lingering or None,
        )
        self._status(
            f"课程会话已结束 state={self._stop_result.state} "
            f"success={self._stop_result.success} "
            f"cleanup={cleanup_status}"
        )
        return self._stop_result

    def _note_final_model(self) -> str:
        from .settings import FINAL_MODEL

        return FINAL_MODEL

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        st = self.note_engine.state
        return {
            "session": self.storage.dir.name,
            "running": self.running,
            "state": self.state.value,
            "transcripts": len(self._transcripts),
            "visual_events": len(self._visuals),
            "vlm_calls": self.visual.vlm_calls if self.visual else 0,
            "vlm_skipped": self.visual.skipped_frames if self.visual else 0,
            "vlm_failed": self.visual.failed_analyses if self.visual else 0,
            "topic": st.current_topic,
            "updates": st.updates,
            "audio": self.audio.last_status if self.audio else "",
            "visual": self.visual.last_status if self.visual else "",
            "error": self.last_error or (self.visual.last_error if self.visual else ""),
            "workers_alive": self._lingering_workers(),
        }
