import time
import threading
import traceback
from typing import List, Tuple

import cv2
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from config import INFER_SIZE
from core.capture import Region, grab
from core.coords import prepare_image
from core.ollama_client import chat, chat_text
from core.prompt import (
    FRAME_DESCRIPTION_SYSTEM, build_frame_prompt,
    VIDEO_AGGREGATE_SYSTEM, build_aggregate_prompt,
)


class VideoAnalyzer(QObject):
    """Background worker: grab frame -> describe with VL -> repeat -> aggregate."""

    # Live progress signals (so the UI can show what is happening right now)
    status = pyqtSignal(str)                            # human-readable status line
    # (idx, timestamp_sec, description, jpeg_bytes)
    frame_done = pyqtSignal(int, float, str, bytes)
    # (final_summary, timeline_as_formatted_string)
    summary_done = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, region: Region, query: str, interval_s: float, max_frames: int):
        super().__init__()
        self._region = region
        self._query = query
        self._interval = max(0.0, float(interval_s))
        self._max_frames = max(1, int(max_frames))
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.status.emit(
                f"Video worker started. Interval={self._interval}s, max_frames={self._max_frames}."
            )
            timeline: List[Tuple[float, str]] = []
            t0 = time.monotonic()
            idx = 0

            while not self._stop_evt.is_set() and idx < self._max_frames:
                frame_idx_display = idx + 1
                frame_start = time.monotonic()

                self.status.emit(f"Grabbing frame {frame_idx_display}...")
                frame: np.ndarray = grab(self._region)
                infer_img, _ = prepare_image(frame, self._region, INFER_SIZE)

                self.status.emit(
                    f"Frame {frame_idx_display}: sending to Qwen2.5-VL (this typically takes 5-15s)..."
                )
                desc = chat(
                    infer_img,
                    FRAME_DESCRIPTION_SYSTEM,
                    build_frame_prompt(self._query),
                    temperature=0.2,
                    as_json=False,
                )
                desc = (desc or "").replace("\n", " ").strip()
                ts = round(time.monotonic() - t0, 1)
                timeline.append((ts, desc))

                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                jpeg = buf.tobytes() if ok else b""
                self.frame_done.emit(idx, ts, desc, jpeg)

                idx += 1
                if self._stop_evt.is_set():
                    break

                elapsed = time.monotonic() - frame_start
                remaining = self._interval - elapsed
                if remaining > 0:
                    self.status.emit(f"Waiting {remaining:.1f}s before next frame...")
                    self._stop_evt.wait(remaining)

            if not timeline:
                self.summary_done.emit("No frames were captured.", "")
                return

            self.status.emit(
                f"Capture finished ({len(timeline)} frame(s)). Aggregating answer with text model..."
            )
            timeline_str = "\n".join(f"[t={ts}s] {d}" for ts, d in timeline)
            summary = chat_text(
                VIDEO_AGGREGATE_SYSTEM,
                build_aggregate_prompt(self._query, timeline_str),
                temperature=0.3,
            )
            self.summary_done.emit(summary, timeline_str)

        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")
