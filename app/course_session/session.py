"""CourseSession: thin orchestrator gluing audio bridge, visual stream,
note fusion loop and storage. No new capture/inference machinery.

Round 3/4: unified cleanup core path — stop/fatal/startup-exception all
funnel through the same stop_workers + release_models + verify_ps sequence.
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
    RECENT_TRANSCRIPT_EVENTS,
    RECENT_VISUAL_EVENTS,
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
        # Round 12: monotonic attempt id for realtime_fusion_status.jsonl.
        self._fuse_attempt_id = 0

        self.audio: Optional[AudioBridge] = None
        self.visual: Optional[VisualStream] = None
        self.last_error: str = ""

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start audio + visual + fusion. Blocks until whisper is loaded.

        Round 4:
        - validate_model_config() rejects dangerous realtime/27B role configs.
        - preflight_cleanup() fail-closed on /api/ps unknown before workers.
        - Any startup exception funnels through unified _cleanup_core.
        """
        from .settings import describe_model_roles, validate_model_config

        try:
            validate_model_config()
        except ValueError as e:
            self.last_error = f"model config rejected: {e}"
            self.state = SessionState.FAILED
            raise RuntimeError(f"Cannot start session: {e}") from e
        self._status("模型角色:\n" + describe_model_roles())

        from .final_summary import preflight_cleanup

        pf_ok, pf_msg = preflight_cleanup()
        if not pf_ok:
            self.last_error = f"preflight cleanup failed: {pf_msg}"
            self.state = SessionState.FAILED
            raise RuntimeError(
                f"Cannot start session: {pf_msg}. "
                "Previous models must be verified/unloaded before realtime phase."
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
            # Round 10: point instrumentation sink BEFORE start so load/callback
            # events land in this session dir (no-op when INSTRUMENTATION off).
            try:
                from .settings import INSTRUMENTATION

                if INSTRUMENTATION:
                    self.audio.set_instrumentation_path(
                        self.storage.dir / "instrumentation.jsonl"
                    )
                    self._status("INSTRUMENTATION=on → instrumentation.jsonl")
            except Exception:
                log.error("set_instrumentation_path failed", exc_info=True)
            self._status("启动音频与 Whisper…")
            self.audio.start()
            self.storage.set_transcript_writer_path(self.audio.writer_path)
        except Exception as e:
            self.last_error = f"audio start failed: {e}"
            self.running = False
            self.state = SessionState.FAILED
            self._startup_cleanup(stop_audio=True, stop_visual=False, stop_fusion=False)
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
            self._startup_cleanup(stop_audio=True, stop_visual=True, stop_fusion=False)
            raise

        try:
            self._stop_fusion.clear()
            self._fusion_thread = threading.Thread(
                target=self._fusion_loop, name="FusionLoop", daemon=True
            )
            self._fusion_thread.start()
            self._flag_thread = threading.Thread(
                target=self._flag_loop, name="StopFlagWatch", daemon=True
            )
            self._flag_thread.start()
        except Exception as e:
            self.last_error = f"fusion/flag start failed: {e}"
            self.running = False
            self.state = SessionState.FAILED
            self._startup_cleanup(stop_audio=True, stop_visual=True, stop_fusion=True)
            raise

        self._status(
            f"课程会话已开始：{self.storage.dir.name} "
            f"(VLM={self.visual._model}, fusion={self.note_engine.model}, "
            f"whisper={self.audio.last_status})"
        )

    def _startup_cleanup(
        self,
        stop_audio: bool,
        stop_visual: bool,
        stop_fusion: bool,
    ) -> None:
        """Unified cleanup for startup exceptions (stop started workers + release)."""
        try:
            self._cleanup_core(
                stop_visual=stop_visual,
                stop_audio=stop_audio,
                stop_fusion=stop_fusion,
                release_models=True,
                wait_final_summary=False,
                failed=True,
            )
        except Exception:
            log.error("startup cleanup failed\n%s", traceback.format_exc())
            self.storage.clear_active_marker()

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
                # Round 12: never let one bad tick kill the fusion thread.
                # Audio/visual/session state stay independent of fusion errors.
                log.error("fusion loop error\n%s", traceback.format_exc())

    def _fuse_once(self) -> None:
        """Realtime fusion tick: recent events -> incremental state update.

        Round 6: snapshot pending, call fuse, consume ONLY on success.
        Failure keeps pending so Final Fusion can catch the tail.
        Never registers FINAL_MODEL here.

        Round 12: write one structured line to realtime_fusion_status.jsonl
        per attempt (always on). Never sets SessionState.FAILED and never
        stops capture on fusion failure/skip/exception.

        Round 13: fuse/consume only the prompt window (last RECENT_* events);
        older pending stay for later ticks / Final Fusion. Success identity-
        consumes only that window, never the full backlog.
        """
        with self._lock:
            if not self._unfused_transcripts and not self._unfused_visuals:
                # No pending: nothing to fuse. Absence of a status line
                # means "no pending", not "silently failing".
                return
            # Full pending counts = backlog evidence. Window = what the model
            # actually sees (matches NoteEngine last-RECENT_* slicing).
            pending_tr = len(self._unfused_transcripts)
            pending_vi = len(self._unfused_visuals)
            tr = list(self._unfused_transcripts[-RECENT_TRANSCRIPT_EVENTS:])
            vi = list(self._unfused_visuals[-RECENT_VISUAL_EVENTS:])
            fused_tr = len(tr)
            fused_vi = len(vi)

        self._fuse_attempt_id += 1
        attempt_id = self._fuse_attempt_id
        t0 = time.monotonic()
        updated = False
        session_exc_type: str | None = None
        session_exc_msg: str | None = None

        try:
            updated = bool(self.note_engine.fuse(tr, vi))
        except Exception as e:
            # fuse() itself is not supposed to raise (Round 12); belt-and-suspenders
            # so a coding error here still cannot drop pending or fail the session.
            session_exc_type = type(e).__name__
            session_exc_msg = str(e)[:300]
            log.error("fuse raised (pending preserved)\n%s", traceback.format_exc())
            updated = False

        latency_ms = int((time.monotonic() - t0) * 1000)

        if updated:
            # Round 13: consume ONLY the prompt-window identities. Backlog
            # older than RECENT_* stays pending for later ticks / Final Fusion.
            with self._lock:
                self._unfused_transcripts = [
                    e for e in self._unfused_transcripts
                    if not any(e is x for x in tr)
                ]
                self._unfused_visuals = [
                    e for e in self._unfused_visuals
                    if not any(e is x for x in vi)
                ]
            mark_model_loaded(self.note_engine.model)
        # failure/skip: leave pending intact for Final Fusion

        nf = getattr(self.note_engine, "last_fuse", None) or {}
        if session_exc_type:
            result = "exception"
            exception_type = session_exc_type
            error_message = session_exc_msg
        else:
            result = nf.get("result") or ("success" if updated else "failure")
            exception_type = nf.get("exception_type")
            error_message = nf.get("error_message")
        input_chars = nf.get("input_chars") or sum(
            len(getattr(e, "text", "") or "") for e in tr
        ) + sum(len(getattr(e, "text", "") or "") for e in vi)

        # Round 12 status keys + Round 13 diagnostic sizes. Status stays
        # privacy-safe: counts/chars/enum only, never full prompt/response.
        record = {
            "timestamp": time.time(),
            "attempt_id": attempt_id,
            "pending_transcript_count": pending_tr,
            "pending_visual_count": pending_vi,
            "input_chars": int(input_chars),
            "result": result,
            "latency_ms": latency_ms,
            "exception_type": exception_type,
            "error_message": error_message,
            "model": str(nf.get("model") or self.note_engine.model or ""),
            "prompt_chars": int(nf.get("prompt_chars") or 0),
            "transcript_chars": int(nf.get("transcript_chars") or 0),
            "visual_chars": int(nf.get("visual_chars") or 0),
            "response_chars": int(nf.get("response_chars") or 0),
            "parse_stage": nf.get("parse_stage"),
            "http_status": nf.get("http_status"),
            "fused_transcript_count": fused_tr,
            "fused_visual_count": fused_vi,
        }
        self._write_fusion_status(record)

        st = self.note_engine.state
        self._status(
            f"增量笔记 #{st.updates}: 主题={st.current_topic or '-'} "
            f"概念={len(st.current_concepts)} 公式={len(st.formulas)} "
            f"强调={len(st.teacher_emphasis)}"
            + ("" if updated else f"（{result}，pending 保留）")
        )

    def _write_fusion_status(self, record: dict) -> None:
        """Best-effort status append; diagnostic only — never fails fusion."""
        try:
            self.storage.append_realtime_fusion_status(record)
        except Exception:
            log.error("realtime_fusion_status write failed", exc_info=True)

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
            # Realtime fusion worker is stopped here. Leftover pending events
            # are drained later by Final Fusion (FINAL_MODEL) only after
            # release + /api/ps gate — never via the realtime fusion tick.

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

        # Order (Round 5): stop workers -> join -> release realtime models
        # -> /api/ps verify -> ONLY THEN final fusion + summary (27B)
        # -> release FINAL_MODEL -> /api/ps verify.
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

        # Verify /api/ps is clean of realtime models before any FINAL_MODEL use.
        if cleanup_status == CleanupStatus.CLEANUP_OK:
            from .final_summary import can_load_final_model

            allowed, reason = can_load_final_model()
            if not allowed:
                cleanup_status = CleanupStatus.CLEANUP_FAILED
                stop_error = (stop_error + "; " if stop_error else "") + f"ps gate: {reason}"
                wait_final_summary = False
                log.error("ps gate before final phase: %s", reason)

        # Audio-fatal path: no full summary, but always write partial notes.
        if failed or self._audio_failed or (self.last_error or "").startswith("audio fatal"):
            wait_final_summary = False

        # Cleanup FAILED -> hard gate: never load FINAL_MODEL (constraint 3).
        if cleanup_status != CleanupStatus.CLEANUP_OK:
            wait_final_summary = False

        fusion_status: Optional[str] = None
        final_release_ok = True
        final_release_errs: list[str] = []

        if wait_final_summary:
            from .final_summary import run_final_phase

            # Drain pending events under lock BEFORE final phase (post-join).
            with self._lock:
                pend_tr = self._unfused_transcripts[:]
                pend_vi = self._unfused_visuals[:]
                self._unfused_transcripts.clear()
                self._unfused_visuals.clear()

            self._status(
                f"正在 Final Fusion + 课后总结（{self._note_final_model()} 独占加载）…"
            )
            try:
                fusion_status, summary_path, final_errs = run_final_phase(
                    self.storage,
                    self._status,
                    pending_transcripts=pend_tr,
                    pending_visuals=pend_vi,
                    note_engine=self.note_engine,
                )
                if final_errs:
                    msg = "; ".join(final_errs)
                    stop_error = (stop_error + "; " if stop_error else "") + msg
                    self.last_error = self.last_error or msg
                    if any(
                        "release" in e.lower()
                        or "resident" in e.lower()
                        or "cannot verify" in e.lower()
                        for e in final_errs
                    ):
                        cleanup_status = CleanupStatus.CLEANUP_FAILED
                    if fusion_status == "failure":
                        log.error("final fusion failed: %s", msg)
                        # Ensure partial artifact exists even if run_final_phase
                        # could not write it (e.g. exception path).
                        if not self.storage.partial_notes_path.exists():
                            try:
                                p = self.storage.write_partial_notes(reason=msg)
                                summary_path = str(p)
                            except Exception:
                                log.error("partial after fusion failure", exc_info=True)
                    if summary_path and str(summary_path).endswith("final_summary.md"):
                        self._status(f"完成: {summary_path}")
                    elif summary_path:
                        self._status(f"部分完成: {summary_path}")
                    else:
                        self._status(f"课后阶段失败: {msg}")
                elif summary_path:
                    self._status(f"完成: {summary_path}")
                if fusion_status:
                    self._status(f"Final Fusion status={fusion_status}")
            except Exception as e:
                stop_error = f"final phase failed: {e}"
                self.last_error = stop_error
                log.error("final phase failed\n%s", traceback.format_exc())
                self._status(f"课后阶段失败: {e}")
                # still attempt FINAL_MODEL release if run_final_phase aborted early
                try:
                    from .final_summary import release_final_model

                    final_release_ok, final_release_errs = release_final_model()
                    if not final_release_ok:
                        cleanup_status = CleanupStatus.CLEANUP_FAILED
                        stop_error = (
                            stop_error + "; "
                        ) + "final release after error: " + (
                            "; ".join(final_release_errs) or "unknown"
                        )
                except Exception:
                    cleanup_status = CleanupStatus.CLEANUP_FAILED
                    log.error("release_final_model after final-phase error", exc_info=True)
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
                # final_summary.md only counts on fusion SUCCESS / NO-OP path.
                success = success and summary_path is not None and (
                    self.storage.final_summary_path.exists()
                    or (summary_path and str(summary_path).endswith("final_summary.md"))
                )
                if fusion_status == "failure":
                    success = False
            elif summary_path:
                # wait_final_summary=False clean stop: partial is enough
                success = success and Path(summary_path).exists()
            # FINAL_MODEL unload failure → not STOPPED success (Round 5).
            if not final_release_ok or final_release_errs:
                success = False
                if cleanup_status == CleanupStatus.CLEANUP_OK:
                    cleanup_status = CleanupStatus.CLEANUP_FAILED
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
