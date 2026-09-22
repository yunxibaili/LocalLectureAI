"""CourseSession: thin orchestrator gluing audio bridge, visual stream,
note fusion loop and storage. No new capture/inference machinery.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Callable, List, Optional

from .events import TranscriptEvent, VisualEvent
from .note_engine import NoteEngine
from .settings import FUSION_INTERVAL_S
from .storage import SessionStorage
from .audio_bridge import AudioBridge
from .visual import VisualStream

# Region comes from QLens
from core.capture import Region  # noqa: E402 (upstream reuse)

log = logging.getLogger(__name__)


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

        self.audio: Optional[AudioBridge] = None
        self.visual: Optional[VisualStream] = None
        self.last_error: str = ""

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start audio + visual + fusion. Blocks until whisper is loaded."""
        self.running = True
        self.storage.ensure_live_header(
            f"课程笔记 — {time.strftime('%Y-%m-%d %H:%M')}"
        )

        # Audio (Hearsay assembly). Blocking (model load).
        self.audio = AudioBridge(
            on_event=self._on_transcript,
            transcript_dir=self.storage.dir,
            t0=self._t0,
            on_fatal=self._on_audio_fatal,
        )
        self._status("启动音频与 Whisper…")
        self.audio.start()
        self.storage.set_transcript_writer_path(self.audio.writer_path)

        # Visual (QLens capture + VLM). Non-blocking thread.
        self.visual = VisualStream(
            region=self.region,
            on_event=self._on_visual,
            t0=self._t0,
        )
        self._status("启动视觉流…")
        self.visual.start()

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
        self.last_error = f"audio fatal: {exc}"
        self._status(f"音频致命错误: {exc}")

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
        st = self.note_engine.state
        self._status(
            f"增量笔记 #{st.updates}: 主题={st.current_topic or '-'} "
            f"概念={len(st.current_concepts)} 公式={len(st.formulas)} "
            f"强调={len(st.teacher_emphasis)}"
            + ("" if updated else "（skip/失败）")
        )

    # ------------------------------------------------------------------
    def stop(self, wait_final_summary: bool = True) -> None:
        """Stop streams, then (optionally) generate final summary."""
        if self._finalizing:
            return
        self._finalizing = True
        self._status("正在停止视觉流…")
        if self.visual:
            self.visual.stop()
            self.visual.join(timeout=30)

        self._status("正在停止音频与 Whisper…")
        if self.audio:
            self.audio.stop()

        self._stop_fusion.set()
        if self._fusion_thread:
            self._fusion_thread.join(timeout=FUSION_INTERVAL_S + 60)

        # final fusion for any leftovers
        try:
            self._fuse_once()
        except Exception:
            log.error("final fusion failed", exc_info=True)

        self.storage.finalize_transcript()
        self.running = False

        if wait_final_summary:
            from .final_summary import generate_final_summary

            self._status("正在生成课后总结（qwen38-27b 独占加载）…")
            try:
                path = generate_final_summary(self.storage, self._status)
                self._status(f"完成: {path}")
            except Exception as e:
                self.last_error = f"final summary failed: {e}"
                log.error("final summary failed\n%s", traceback.format_exc())
                self._status(f"课后总结失败: {e}")
        self._status("课程会话已结束")

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        st = self.note_engine.state
        return {
            "session": self.storage.dir.name,
            "running": self.running,
            "transcripts": len(self._transcripts),
            "visual_events": len(self._visuals),
            "vlm_calls": self.visual.vlm_calls if self.visual else 0,
            "vlm_skipped": self.visual.skipped_frames if self.visual else 0,
            "topic": st.current_topic,
            "updates": st.updates,
            "audio": self.audio.last_status if self.audio else "",
            "visual": self.visual.last_status if self.visual else "",
            "error": self.last_error or (self.visual.last_error if self.visual else ""),
        }
