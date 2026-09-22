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

        self.last_error: str = ""
        self.last_status: str = "idle"
        self.chunks_transcribed = 0
        self.events_emitted = 0

    @property
    def writer_path(self) -> Path:
        return self._writer_path

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Load whisper, start pipeline + recorder + poller (blocking until ready)."""
        from hearsay.audio.recorder import AudioRecorder              # reuse
        from hearsay.transcription.engine import TranscriptionEngine  # reuse
        from hearsay.transcription.pipeline import TranscriptionPipeline  # reuse
        from hearsay.transcription.gpu_detect import detect_gpu       # reuse

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
            device, compute = "cpu", "int8"
            engine = TranscriptionEngine(
                model_name=WHISPER_MODEL,
                device=device,
                compute_type=compute,
                language=WHISPER_LANGUAGE,
                vad_filter=True,
            )
            engine.load()

        self._engine = engine
        self._pipeline = TranscriptionPipeline(
            audio_queue=self._audio_queue,
            transcript_queue=self._transcript_queue,
            engine=engine,
        )
        self._pipeline.start()

        self._recorder = AudioRecorder(
            audio_queue=self._audio_queue,
            source=AUDIO_SOURCE,
            on_fatal=self._handle_fatal,
            on_no_audio=self._handle_no_audio,
        )
        self._recorder.start()

        self._recording = True
        self._stop_poll.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="TranscriptPoll", daemon=True
        )
        self._poll_thread.start()
        self.last_status = f"recording ({AUDIO_SOURCE}, whisper {device})"
        log.info("AudioBridge started: %s", self.last_status)

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
            return

        session_t = time.monotonic() - self._t0
        # Merge segments of this window into a couple of TranscriptEvents
        # (one per segment keeps alignment precise; windows are 30s).
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
            self._on_event(ev)

        self.chunks_transcribed += 1
        preview = " ".join(s.get("text", "") for s in result.segments)[:80]
        self.last_status = f"chunk {result.chunk_index}: {preview}"

    def _handle_fatal(self, exc: Exception) -> None:
        self.last_error = f"recorder fatal: {exc}"
        log.error("AudioRecorder fatal: %s", exc)
        if self._on_fatal:
            try:
                self._on_fatal(exc)
            except Exception:
                pass

    def _handle_no_audio(self) -> None:
        self.last_status = "warning: no audio captured (device silent)"
        log.warning("AudioBridge: no audio captured")
        if self._on_no_audio:
            try:
                self._on_no_audio()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Teardown mirroring HearsayApp._teardown_recording (no UI)."""
        self._recording = False
        self._stop_poll.set()

        recorder, pipeline, engine = self._recorder, self._pipeline, self._engine
        self._recorder = self._pipeline = self._engine = None

        if recorder:
            recorder.stop()
            recorder.join(timeout=5)
            waited = 5
            while recorder.is_alive() and waited < 60:
                recorder.join(timeout=5)
                waited += 5

        if pipeline:
            pipeline.stop()
            pipeline.join(timeout=60)

        # drain remaining results before unload
        if self._poll_thread:
            self._poll_thread.join(timeout=3)

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
            except Exception:
                log.error("engine unload failed", exc_info=True)

        self.last_status = "stopped"
        log.info("AudioBridge stopped (%d events)", self.events_emitted)
