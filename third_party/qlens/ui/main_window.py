import json
import traceback
from typing import Optional, List

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, pyqtSlot
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QLabel, QPlainTextEdit, QStatusBar, QFrame, QDialog, QDialogButtonBox,
)

from config import (
    INFER_SIZE, HOTKEY,
    MAX_OBJECTS_DEFAULT, VIDEO_INTERVAL_S_DEFAULT, VIDEO_MAX_FRAMES_DEFAULT,
)
from core.capture import Region, grab
from core.coords import prepare_image, CoordinateMapper
from core.intent import detect_task
from core.ollama_client import chat_with_retry
from core.parser import parse
from core.prompt import SYSTEM_PROMPT, build_user_prompt
from core.region_selector import RegionSelector
from core.video import VideoAnalyzer
from render.draw import draw_bbox, draw_multi_bbox, draw_points
from ui.overlay import Overlay
from ui.result_view import ResultView
from ui.settings_dialog import SettingsDialog, AppSettings


# Idle and active styles for the Video button so it is impossible to miss.
VIDEO_BTN_IDLE = (
    "QPushButton { background-color: #d97706; color: white; font-weight: bold; padding: 6px 14px; }"
    "QPushButton:hover { background-color: #f59e0b; }"
    "QPushButton:disabled { background-color: #555; color: #999; }"
)
VIDEO_BTN_ACTIVE = (
    "QPushButton { background-color: #dc2626; color: white; font-weight: bold; padding: 6px 14px; }"
    "QPushButton:hover { background-color: #ef4444; }"
)

RECORDING_BANNER_STYLE = (
    "background-color: #dc2626; color: white; font-weight: bold; padding: 6px 10px; border-radius: 4px;"
)


class VideoAnswerDialog(QDialog):
    """Popup shown when video analysis finishes."""

    def __init__(self, parent, summary: str, timeline: str, frame_count: int):
        super().__init__(parent)
        self.setWindowTitle("Video Analysis - Answer")
        self.setMinimumWidth(640)
        self.setMinimumHeight(360)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"<b>Frames analyzed: {frame_count}</b>"))
        layout.addWidget(QLabel("<b>Answer:</b>"))

        answer_box = QPlainTextEdit()
        answer_box.setReadOnly(True)
        answer_box.setPlainText(summary)
        answer_box.setMinimumHeight(140)
        answer_box.setStyleSheet("font-size:13px; padding:6px;")
        layout.addWidget(answer_box)

        if timeline:
            layout.addWidget(QLabel("<b>Per-frame timeline:</b>"))
            timeline_box = QPlainTextEdit()
            timeline_box.setReadOnly(True)
            timeline_box.setPlainText(timeline)
            timeline_box.setMaximumHeight(220)
            timeline_box.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
            layout.addWidget(timeline_box)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


class InferenceWorker(QObject):
    """Single-shot worker for localization / multi / counting."""

    finished = pyqtSignal(dict, object, object)
    failed = pyqtSignal(str)

    def __init__(self, region: Region, query: str, max_objects: int):
        super().__init__()
        self._region = region
        self._query = query
        self._max_objects = max_objects

    @pyqtSlot()
    def run(self):
        try:
            crop = grab(self._region)
            infer_img, mapper = prepare_image(crop, self._region, INFER_SIZE)
            forced = detect_task(self._query, self._max_objects)
            user_prompt = build_user_prompt(self._query, forced, self._max_objects)
            raw = chat_with_retry(infer_img, SYSTEM_PROMPT, user_prompt)
            parsed = parse(raw)
            parsed["_raw"] = raw
            self.finished.emit(parsed, mapper, crop)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


COMPACT_HEIGHT = 86
COMPACT_HEIGHT_RECORDING = 120  # extra row for the recording banner


class MainWindow(QMainWindow):
    request_capture = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("qlens")
        self._overlay = Overlay()
        self._selector: Optional[RegionSelector] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[InferenceWorker] = None
        self._video_thread: Optional[QThread] = None
        self._video_worker: Optional[VideoAnalyzer] = None
        self._video_running = False
        self._video_log_lines: List[str] = []
        self._video_frame_count = 0

        self._settings = AppSettings(
            max_objects=MAX_OBJECTS_DEFAULT,
            video_interval_s=VIDEO_INTERVAL_S_DEFAULT,
            video_max_frames=VIDEO_MAX_FRAMES_DEFAULT,
        )

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # ----- top bar -----
        top = QHBoxLayout()
        top.setSpacing(6)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(
            'Ask... e.g. "show cat and dog" / "how many people?" / "summarize this video"'
        )
        self.query_edit.setToolTip("Type a question. Press Enter or click 'Capture image' for a single screenshot, or 'Video Mode' for continuous frame analysis.")
        self.query_edit.returnPressed.connect(self.start_capture)

        self.capture_btn = QPushButton(f"Capture image  ({HOTKEY.upper()})")
        self.capture_btn.clicked.connect(self.start_capture)

        self.video_btn = QPushButton("Video Mode")
        self.video_btn.setStyleSheet(VIDEO_BTN_IDLE)
        self.video_btn.setToolTip("Continuous frame-by-frame analysis of a screen region (e.g. a video player).")
        self.video_btn.clicked.connect(self.start_or_stop_video)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedWidth(34)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self.open_settings)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear overlay")
        self.clear_btn.clicked.connect(self._overlay.clear)

        self.toggle_btn = QPushButton("Details ▾")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.toggled.connect(self._set_details_visible)

        top.addWidget(self.query_edit, 1)
        top.addWidget(self.capture_btn)
        top.addWidget(self.video_btn)
        top.addWidget(self.settings_btn)
        top.addWidget(self.clear_btn)
        top.addWidget(self.toggle_btn)
        root.addLayout(top)

        # ----- recording banner (hidden by default) -----
        self.banner = QLabel("")
        self.banner.setStyleSheet(RECORDING_BANNER_STYLE)
        self.banner.setVisible(False)
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.banner)

        # ----- details panel -----
        self.details = QFrame()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 4, 0, 0)
        details_layout.setSpacing(4)
        self.result_view = ResultView()
        details_layout.addWidget(self.result_view, 1)
        details_layout.addWidget(QLabel("Output / debug:"))
        self.debug = QPlainTextEdit()
        self.debug.setReadOnly(True)
        self.debug.setMaximumHeight(220)
        self.debug.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        details_layout.addWidget(self.debug)
        self.details.setVisible(False)
        root.addWidget(self.details, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._set_status_idle()

        QShortcut(QKeySequence("Ctrl+Shift+A"), self, activated=self.start_capture)
        self.request_capture.connect(self.start_capture, Qt.ConnectionType.QueuedConnection)

        self.resize(960, COMPACT_HEIGHT)
        self.setMinimumWidth(720)
        self._apply_compact_constraints()

    # ---------- status helpers ----------
    def _set_status_idle(self) -> None:
        mo = self._settings.max_objects
        mode_hint = f"multi-bbox (max {mo})" if mo > 1 else "single bbox"
        self.statusBar().showMessage(
            f"Ready | mode: {mode_hint} | hotkey: {HOTKEY.upper()}"
        )

    @pyqtSlot(bool)
    def _set_details_visible(self, visible: bool) -> None:
        self.details.setVisible(visible)
        self.toggle_btn.setText("Details ▴" if visible else "Details ▾")
        if visible:
            self.setMinimumHeight(420)
            self.setMaximumHeight(16777215)
            self.resize(max(self.width(), 960), 700)
        else:
            self._apply_compact_constraints()

    def _apply_compact_constraints(self) -> None:
        h = COMPACT_HEIGHT_RECORDING if self._video_running else COMPACT_HEIGHT
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        self.resize(self.width(), h)

    def _ensure_details_open(self) -> None:
        if not self.toggle_btn.isChecked():
            self.toggle_btn.setChecked(True)

    # ---------- settings ----------
    @pyqtSlot()
    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self._settings)
        if dlg.exec():
            self._settings = dlg.values()
            self._set_status_idle()

    # ---------- single-shot capture ----------
    @pyqtSlot()
    def start_capture(self):
        if self._video_running:
            self.statusBar().showMessage("Video Mode is active - press 'STOP VIDEO' first.")
            return
        if self._selector is not None:
            return
        if not self.query_edit.text().strip():
            self.statusBar().showMessage("Type a question first.")
            return

        def on_done(region: Optional[Region]):
            self._selector = None
            if region is None:
                self._set_status_idle()
                return
            self._run_inference(region, self.query_edit.text().strip())

        self._overlay.clear()
        self.statusBar().showMessage("Single image: select a region...  (Esc = cancel)")
        self._selector = RegionSelector(on_done)
        self._selector.showFullScreen()

    def _run_inference(self, region: Region, query: str):
        self.statusBar().showMessage("Running inference (Qwen2.5-VL)...")
        self.capture_btn.setEnabled(False)
        self.video_btn.setEnabled(False)

        self._thread = QThread(self)
        self._worker = InferenceWorker(region, query, self._settings.max_objects)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_inference_done)
        self._worker.failed.connect(self._on_inference_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @pyqtSlot(dict, object, object)
    def _on_inference_done(self, parsed: dict, mapper: CoordinateMapper, crop_bgr):
        self.capture_btn.setEnabled(True)
        self.video_btn.setEnabled(True)
        raw = parsed.pop("_raw", "")
        self.debug.setPlainText(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n\n--- raw ---\n" + raw
        )

        task = parsed["task"]

        if task == "localization":
            label = parsed.get("object", "")
            bbox_crop = mapper.bbox_model_to_crop(parsed["bbox"])
            bbox_screen = mapper.bbox_model_to_screen(parsed["bbox"])
            annotated = draw_bbox(
                crop_bgr, bbox_crop, f"{label} ({parsed.get('confidence', 0):.2f})"
            )
            self.result_view.show_image(annotated)
            self._overlay.show_bbox(bbox_screen, label)
            self.statusBar().showMessage(
                f"Localization: {label}  conf={parsed.get('confidence', 0):.2f}"
            )
            return

        if task == "multi_localization":
            objects = parsed.get("objects", [])
            items_crop = []
            items_screen = []
            for obj in objects:
                bc = mapper.bbox_model_to_crop(obj["bbox"])
                bs = mapper.bbox_model_to_screen(obj["bbox"])
                lbl = f'{obj.get("name", "")} ({obj.get("confidence", 0):.2f})'
                items_crop.append({"bbox_px": bc, "label": lbl})
                items_screen.append({"bbox_screen_px": bs, "label": lbl})
            annotated = draw_multi_bbox(crop_bgr, items_crop)
            self.result_view.show_image(annotated)
            self._overlay.show_multi_bbox(items_screen)
            names = ", ".join(o.get("name", "") for o in objects) or "-"
            self.statusBar().showMessage(
                f"Multi-localization: {len(objects)} object(s) - {names}"
            )
            return

        label = parsed.get("object", "")
        instances_crop = []
        instances_screen = []
        for inst in parsed["instances"]:
            cx, cy = mapper.model_to_crop(inst["point"][0], inst["point"][1])
            sx, sy = mapper.model_to_screen(inst["point"][0], inst["point"][1])
            instances_crop.append({"id": inst["id"], "point_px": (cx, cy)})
            instances_screen.append({"id": inst["id"], "point_px": (sx, sy)})
        annotated = draw_points(crop_bgr, instances_crop, label)
        self.result_view.show_image(annotated)
        self._overlay.show_points(instances_screen, label)
        self.statusBar().showMessage(f"Counting: {label} = {parsed['count']}")

    @pyqtSlot(str)
    def _on_inference_failed(self, msg: str):
        self.capture_btn.setEnabled(True)
        self.video_btn.setEnabled(True)
        self.statusBar().showMessage("Inference error (see details).")
        self.debug.setPlainText(msg)
        self._ensure_details_open()

    # ---------- video analysis ----------
    @pyqtSlot()
    def start_or_stop_video(self):
        if self._video_running:
            self._stop_video()
            return
        if self._selector is not None:
            return
        if not self.query_edit.text().strip():
            self.statusBar().showMessage("Type a question about the video first.")
            return

        def on_done(region: Optional[Region]):
            self._selector = None
            if region is None:
                self._set_status_idle()
                return
            self._run_video(region, self.query_edit.text().strip())

        self._overlay.clear()
        self.statusBar().showMessage("Video Mode: select the video region...  (Esc = cancel)")
        self._selector = RegionSelector(on_done)
        self._selector.showFullScreen()

    def _run_video(self, region: Region, query: str) -> None:
        self._video_running = True
        self._video_log_lines = []
        self._video_frame_count = 0

        # banner makes it visually obvious that video mode is active
        self.banner.setText(
            "RECORDING VIDEO  -  capturing frames continuously  -  press the red 'STOP VIDEO' button to finish"
        )
        self.banner.setVisible(True)

        self.debug.setPlainText(
            "Video Mode started.\n"
            f"Query: {query}\n"
            f"Region: x={region.x}, y={region.y}, w={region.w}, h={region.h}\n"
            f"Mode: {'CONTINUOUS (back-to-back)' if self._settings.video_interval_s <= 0 else f'every {self._settings.video_interval_s}s'}\n"
            f"Max frames: {self._settings.video_max_frames}\n"
            "-----------------------------------------\n"
        )
        self.capture_btn.setEnabled(False)
        self.settings_btn.setEnabled(False)
        self.video_btn.setText("STOP VIDEO")
        self.video_btn.setStyleSheet(VIDEO_BTN_ACTIVE)
        self.statusBar().showMessage("Video Mode active - first frame may take 10-15s.")

        self._video_thread = QThread(self)
        self._video_worker = VideoAnalyzer(
            region=region, query=query,
            interval_s=self._settings.video_interval_s,
            max_frames=self._settings.video_max_frames,
        )
        self._video_worker.moveToThread(self._video_thread)
        self._video_thread.started.connect(self._video_worker.run)
        self._video_worker.status.connect(self._on_video_status)
        self._video_worker.frame_done.connect(self._on_video_frame)
        self._video_worker.summary_done.connect(self._on_video_summary)
        self._video_worker.failed.connect(self._on_video_failed)
        self._video_worker.summary_done.connect(self._video_thread.quit)
        self._video_worker.failed.connect(self._video_thread.quit)
        self._video_thread.finished.connect(self._video_thread.deleteLater)
        self._video_thread.start()

    def _stop_video(self) -> None:
        if self._video_worker is not None:
            self.video_btn.setEnabled(False)
            self.video_btn.setText("Aggregating...")
            self.banner.setText(
                f"STOPPING - aggregating answer from {self._video_frame_count} frame(s)..."
            )
            self.statusBar().showMessage(
                f"Stopping - waiting for current frame to finish, then aggregating {self._video_frame_count} frame(s)."
            )
            self._video_worker.stop()

    def _reset_video_ui(self) -> None:
        self._video_running = False
        self.capture_btn.setEnabled(True)
        self.settings_btn.setEnabled(True)
        self.video_btn.setEnabled(True)
        self.video_btn.setText("Video Mode")
        self.video_btn.setStyleSheet(VIDEO_BTN_IDLE)
        self.banner.setVisible(False)

    @pyqtSlot(str)
    def _on_video_status(self, msg: str) -> None:
        self._video_log_lines.append(f"[status] {msg}")
        self.debug.setPlainText("\n".join(self._video_log_lines))
        self.banner.setText(f"RECORDING  -  {msg}  -  press STOP VIDEO to finish")
        self.statusBar().showMessage(msg)

    @pyqtSlot(int, float, str, bytes)
    def _on_video_frame(self, idx: int, ts: float, desc: str, jpeg: bytes):
        self._video_frame_count = idx + 1
        line = f"[t={ts}s] frame {idx + 1}: {desc}"
        self._video_log_lines.append(line)
        max_n = self._settings.video_max_frames
        self.statusBar().showMessage(
            f"Video: frame {idx + 1}/{max_n} done. Press 'STOP VIDEO' to aggregate."
        )
        self.banner.setText(
            f"RECORDING  -  frame {idx + 1}/{max_n} done  -  press STOP VIDEO to finish"
        )
        self.debug.setPlainText("\n".join(self._video_log_lines))

        if jpeg:
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame_bgr is not None:
                self.result_view.show_image(frame_bgr)

    @pyqtSlot(str, str)
    def _on_video_summary(self, summary: str, timeline: str):
        self._reset_video_ui()
        n = self._video_frame_count
        self.statusBar().showMessage(f"Video analysis done - {n} frame(s) analyzed.")
        self.debug.setPlainText(
            f"=== ANSWER ===\n{summary}\n\n=== TIMELINE ===\n{timeline}"
        )
        dlg = VideoAnswerDialog(self, summary, timeline, n)
        dlg.exec()

    @pyqtSlot(str)
    def _on_video_failed(self, msg: str):
        self._reset_video_ui()
        self.statusBar().showMessage("Video analysis error (see details).")
        self.debug.setPlainText(msg)
        self._ensure_details_open()

    def closeEvent(self, e):
        try:
            if self._video_worker is not None:
                self._video_worker.stop()
        except Exception:
            pass
        try:
            self._overlay.close()
        except Exception:
            pass
        super().closeEvent(e)
