"""Audio bridge: wire Hearsay's recorder + pipeline into TranscriptEvents.

Reuses (unmodified):
  - hearsay.audio.recorder.AudioRecorder   (WASAPI loopback, 30s windows)
  - hearsay.transcription.pipeline.TranscriptionPipeline
  - hearsay.transcription.engine.TranscriptionEngine (faster-whisper)
  - hearsay.output.markdown_writer.MarkdownWriter
  - hearsay.utils.threading_utils.StoppableThread

Assembly order mirrors HearsayApp._start_recording / _teardown_recording
(engine.load -> pipeline.start -> recorder.start; stop: recorder -> pipeline
-> engine.unload -> writer.finalize) without tray/tkinter UI.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .settings import (
    AUDIO_SOURCE,
    WHISPER_COMPUTE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)
from .events import TranscriptEvent

log = logging.getLogger(__name__)

try:
    from hearsay.utils import instrumentation as _inst
except Exception:  # pragma: no cover
    class _InstFallback:
        @staticmethod
        def emit(*a, **k):
            return None

        @staticmethod
        def enabled() -> bool:
            return False

        @staticmethod
        def set_output(path) -> None:
            return None

    _inst = _InstFallback()


class AudioBridge:
    def __init__(
        self,
        on_event: Callable[[TranscriptEvent], None],
        transcript_dir: Path,
        t0: Optional[float] = None,
        on_fatal: Optional[Callable[[Exception], None]] = None,
        on_no_audio: Optional[Callable[[], None]] = None,
    ) -> None:
        from hearsay.output.markdown_writer import MarkdownWriter  # reuse

        self._on_event = on_event
        self._on_fatal = on_fatal
        self._on_no_audio = on_no_audio
        self._t0 = t0 if t0 is not None else time.monotonic()

        self._audio_queue: queue.Queue = queue.Queue(maxsize=10)
        self._transcript_queue: queue.Queue = queue.Queue()

        # MarkdownWriter names its own file transcript_<ts>.md inside dir;
        # storage renames it to transcript.md at finalize.
        self._writer = MarkdownWriter(transcript_dir)
        self._writer_path = Path(self._writer.file_path)

        self._engine = None
        self._pipeline = None
        self._recorder = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_poll = threading.Event()
        self._recording = False
        self._started = False
        self._stop_lock = threading.Lock()
        self._stopped = False

        self.last_error: str = ""
        self.last_status: str = "idle"
        self.chunks_transcribed = 0
        self.events_emitted = 0
        self._instrumentation_path: Optional[Path] = None

    @property
    def writer_path(self) -> Path:
        return self._writer_path

    def set_instrumentation_path(self, path: Path | str) -> None:
        """Point Round 10 JSONL sink at the session dir (no-op if disabled)."""
        self._instrumentation_path = Path(path)
        if _inst.enabled():
            _inst.set_output(self._instrumentation_path)

    def workers_alive(self) -> list[str]:
        """Names of still-running worker threads (for stop-gate checks).

        Only returns names after verifying is_alive() is True. Uses retained
        references — never cleared until after join confirms death.
        """
        out: list[str] = []
        if self._recorder is not None and self._recorder.is_alive():
            out.append("recorder")
        if self._pipeline is not None and self._pipeline.is_alive():
            out.append("pipeline")
        if self._poll_thread is not None and self._poll_thread.is_alive():
            out.append("poll")
        return out

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Load whisper, start pipeline + recorder + poller (blocking until ready).

        On any failure after a partial start, tears down in reverse order
        (recorder -> pipeline -> engine) so no half-started workers leak.
        """
        from hearsay.audio.recorder import AudioRecorder              # reuse
        from hearsay.transcription.engine import TranscriptionEngine  # reuse
        from hearsay.transcription.pipeline import TranscriptionPipeline  # reuse
        from hearsay.transcription.gpu_detect import detect_gpu       # reuse

        _inst.emit("audio_bridge_start_begin")
        _start_t0 = time.perf_counter()
        device = WHISPER_DEVICE
        compute = WHISPER_COMPUTE
        if device == "auto":
            gpu = detect_gpu()
            if gpu.cuda_available:
                device, compute = "cuda", compute or "float16"
            else:
                device, compute = "cpu", compute or "int8"
        elif not compute:
            compute = "float16" if device == "cuda" else "int8"

        self.last_status = f"loading whisper ({WHISPER_MODEL}, {device}/{compute})..."
        _load_t0 = time.perf_counter()
        engine = TranscriptionEngine(
            model_name=WHISPER_MODEL,
            device=device,
            compute_type=compute,
            language=WHISPER_LANGUAGE,
            vad_filter=True,
        )
        try:
            engine.load()
        except Exception as e:
            # GPU load failure -> CPU fallback (documented in TEST_REPORT)
            log.warning("whisper load on %s failed (%s); falling back to cpu/int8", device, e)
            self.last_error = f"gpu whisper failed: {e}"
            _inst.emit("engine_load_fallback", device=device, error=str(e))
            device, compute = "cpu", "int8"
            engine = TranscriptionEngine(
                model_name=WHISPER_MODEL,
                device=device,
                compute_type=compute,
                language=WHISPER_LANGUAGE,
                vad_filter=True,
            )
            _load_t0 = time.perf_counter()
            engine.load()

        _bridge_load_ms = int((time.perf_counter() - _load_t0) * 1000)
        _inst.emit(
            "audio_bridge_load_end",
            model=WHISPER_MODEL,
            device=device,
            compute=compute,
            engine_load_duration_ms=_bridge_load_ms,
        )
        self._engine = engine
        pipeline = None
        recorder = None
        try:
            pipeline = TranscriptionPipeline(
                audio_queue=self._audio_queue,
                transcript_queue=self._transcript_queue,
                engine=engine,
            )
            pipeline.start()
            self._pipeline = pipeline
            _inst.emit("pipeline_started")

            recorder = AudioRecorder(
                audio_queue=self._audio_queue,
                source=AUDIO_SOURCE,
                on_fatal=self._handle_fatal,
                on_no_audio=self._handle_no_audio,
            )
            recorder.start()
            self._recorder = recorder
            _inst.emit("recorder_started_from_bridge", source=AUDIO_SOURCE)
        except Exception:
            # reverse-order rollback: recorder -> pipeline -> engine
            self._rollback_start(recorder, pipeline, engine)
            raise

        self._recording = True
        self._started = True
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="TranscriptPoll", daemon=True
        )
        self._poll_thread.start()
        self.last_status = f"recording ({AUDIO_SOURCE}, whisper {device})"
        log.info("AudioBridge started: %s", self.last_status)
        _inst.emit(
            "audio_bridge_start_end",
            status=self.last_status,
            total_start_ms=int((time.perf_counter() - _start_t0) * 1000),
        )

    def _rollback_start(self, recorder, pipeline, engine) -> None:
        """Best-effort reverse teardown of a partially started stack.

        Uses same stop-then-join-then-clear-ref discipline as stop().
        """
        if recorder is not None:
            try:
                recorder.stop()
                if hasattr(recorder, "join"):
                    recorder.join(timeout=5)
                # keep ref until confirmed dead; if dead, clear
                if hasattr(recorder, "is_alive") and not recorder.is_alive():
                    if self._recorder is recorder:
                        self._recorder = None
            except Exception:
                log.error("rollback recorder failed", exc_info=True)
        if pipeline is not None:
            try:
                pipeline.stop()
                if hasattr(pipeline, "join"):
                    pipeline.join(timeout=15)
                if hasattr(pipeline, "is_alive") and not pipeline.is_alive():
                    if self._pipeline is pipeline:
                        self._pipeline = None
            except Exception:
                log.error("rollback pipeline failed", exc_info=True)
        if engine is not None:
            try:
                engine.unload()
                if self._engine is engine:
                    self._engine = None
            except Exception:
                log.error("rollback engine unload failed", exc_info=True)

    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while not self._stop_poll.is_set():
            try:
                result = self._transcript_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._handle_result(result)
            except Exception:
                log.error("transcript handle failed\n%s", traceback.format_exc())

    def _handle_result(self, result) -> None:
        # Always keep Hearsay's own markdown transcript up to date (reuse).
        try:
            self._writer.append(result)
        except Exception:
            log.error("MarkdownWriter.append failed", exc_info=True)

        if not result.segments:
            _inst.emit(
                "result_empty_segments",
                window_id=getattr(result, "chunk_index", None),
                window_start_s=getattr(result, "window_start", None),
                segment_count=0,
                drop_reason="empty_result_no_transcript_event",
            )
            return

        session_t = time.monotonic() - self._t0
        # Merge segments of this window into a couple of TranscriptEvents
        # (one per segment keeps alignment precise; windows are 30s).
        emitted_here = 0
        for seg in result.segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            ev = TranscriptEvent(
                timestamp=time.time(),
                duration=float(seg.get("end", 0)) - float(seg.get("start", 0)),
                text=text,
                source="system_audio",
                session_t=session_t + float(seg.get("start", 0)),
            )
            self.events_emitted += 1
            emitted_here += 1
            _inst.emit(
                "transcript_event",
                window_id=getattr(result, "chunk_index", None),
                session_t=round(ev.session_t, 3),
                text_length=len(text),
                events_emitted_total=self.events_emitted,
                text_preview=text[:40],
            )
            self._on_event(ev)

        self.chunks_transcribed += 1
        preview = " ".join(s.get("text", "") for s in result.segments)[:80]
        self.last_status = f"chunk {result.chunk_index}: {preview}"
        _inst.emit(
            "result_handled",
            window_id=getattr(result, "chunk_index", None),
            segment_count=len(result.segments),
            events_emitted=emitted_here,
            chunks_transcribed=self.chunks_transcribed,
        )

    def _handle_fatal(self, exc: Exception) -> None:
        self.last_error = f"recorder fatal: {exc}"
        log.error("AudioRecorder fatal: %s", exc)
        _inst.emit("recorder_fatal", error=str(exc))
        if self._on_fatal:
            try:
                self._on_fatal(exc)
            except Exception:
                pass

    def _handle_no_audio(self) -> None:
        self.last_status = "warning: no audio captured (device silent)"
        log.warning("AudioBridge: no audio captured")
        _inst.emit("on_no_audio_bridge", status=self.last_status)
        if self._on_no_audio:
            try:
                self._on_no_audio()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def stop(self) -> bool:
        """Teardown: request stop -> signal -> join -> is_alive verify -> clear refs.

        Hard constraint: references are ONLY cleared after join() confirms
        is_alive() == False. On timeout, references are KEPT and cleanup is
        reported as incomplete (returns False).

        Returns True only if all workers confirmed dead.
        """
        with self._stop_lock:
            if self._stopped:
                # already stopping/stopped — return current liveness state
                return not self.workers_alive()
            self._stopped = True

        self._recording = False
        self._stop_poll.set()

        recorder, pipeline, engine = self._recorder, self._pipeline, self._engine
        cleanup_ok = True

        # recorder: stop -> join loop until dead or timeout
        if recorder:
            try:
                recorder.stop()
                recorder.join(timeout=5)
                waited = 5
                while recorder.is_alive() and waited < 60:
                    recorder.join(timeout=5)
                    waited += 5
                if recorder.is_alive():
                    log.error("recorder still alive after 60s join")
                    cleanup_ok = False
                    # DO NOT clear reference — keep for workers_alive()
                else:
                    # confirmed dead — clear reference
                    if self._recorder is recorder:
                        self._recorder = None
            except Exception:
                log.error("recorder stop failed", exc_info=True)
                cleanup_ok = False

        # pipeline: stop -> join until dead or timeout
        if pipeline:
            try:
                pipeline.stop()
                pipeline.join(timeout=60)
                if pipeline.is_alive():
                    log.error("pipeline still alive after 60s join")
                    cleanup_ok = False
                    # keep reference
                else:
                    if self._pipeline is pipeline:
                        self._pipeline = None
            except Exception:
                log.error("pipeline stop failed", exc_info=True)
                cleanup_ok = False

        # poll thread: signal already set; join
        if self._poll_thread:
            self._poll_thread.join(timeout=3)
            if self._poll_thread.is_alive():
                log.error("poll thread still alive after join")
                cleanup_ok = False
                # keep reference
            else:
                if self._poll_thread is not None:
                    self._poll_thread = None

        # drain remaining results before unload
        try:
            while True:
                result = self._transcript_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass

        duration = time.monotonic() - self._t0 if self._t0 else None
        try:
            self._writer.finalize(total_duration=duration)
            self._writer.post_process()
        except Exception:
            log.error("writer finalize failed", exc_info=True)

        if engine:
            try:
                engine.unload()
                # engine unload doesn't have a thread to join; clear after unload
                if self._engine is engine:
                    self._engine = None
            except Exception:
                log.error("engine unload failed", exc_info=True)
                # keep reference so it's visible

        self.last_status = "stopped"
        self._started = False
        log.info("AudioBridge stopped (%d events, cleanup_ok=%s)",
                 self.events_emitted, cleanup_ok)
        return cleanup_ok
