"""Minimal PyQt6 GUI: pick region -> start/stop course session.

Deliberately minimal (stability first). Reuses QLens RegionSelector and
its DPI-awareness bootstrap.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import traceback
from pathlib import Path

# DPI awareness BEFORE QApplication (same as QLens main.py)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PyQt6.QtCore import Qt, QObject, pyqtSignal, pyqtSlot  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QPlainTextEdit, QStatusBar,
)

from .settings import VISION_MODEL, FINAL_MODEL, SESSIONS_DIR  # noqa: E402
from .session import CourseSession, StopResult  # noqa: E402

# Reuse QLens region selector (unmodified upstream).
from core.region_selector import RegionSelector  # noqa: E402
from core.capture import Region  # noqa: E402

log = logging.getLogger(__name__)

STYLE_START = (
    "QPushButton { background-color:#16a34a; color:white; font-weight:bold;"
    " padding:8px 18px; border-radius:4px; }"
    "QPushButton:hover { background-color:#15803d; }"
    "QPushButton:disabled { background-color:#555; color:#999; }"
)
STYLE_STOP = (
    "QPushButton { background-color:#dc2626; color:white; font-weight:bold;"
    " padding:8px 18px; border-radius:4px; }"
    "QPushButton:hover { background-color:#ef4444; }"
    "QPushButton:disabled { background-color:#555; color:#999; }"
)


def format_stop_result(r: StopResult) -> str:
    """Honest user-facing line for a StopResult (never fake success).

    final_summary success requires: r.success AND summary_path endswith
    final_summary.md AND file exists AND size > 0.
    """
    def _final_summary_valid(path: str | None) -> bool:
        if not path or not path.endswith("final_summary.md"):
            return False
        try:
            p = Path(path)
            return p.exists() and p.stat().st_size > 0
        except Exception:
            return False

    if r.success and _final_summary_valid(r.summary_path):
        base = f"课程已结束，final_summary.md 已生成：{r.summary_path}"
        if r.partial_available:
            base += "（另有 partial_notes.md）"
        return base
    if r.success and r.partial_available and r.summary_path:
        return f"课程已结束（部分笔记）：{r.summary_path}"
    # Check for cleanup failure specifically
    if r.error and ("cleanup" in r.error.lower() or "resident" in r.error.lower()
                    or "CLEANUP_FAILED" in r.error):
        base = "实时资源未完全清理，未生成最终总结"
        if r.error:
            base += f" — {r.error}"
        if r.partial_available and r.summary_path:
            base += f" | 部分笔记可用: {r.summary_path}"
        return base
    parts = [f"课程结束状态: {r.state}"]
    if r.error:
        parts.append(f"错误: {r.error}")
    if r.partial_available and r.summary_path:
        parts.append(f"部分笔记可用: {r.summary_path}")
    elif r.partial_available:
        parts.append("部分笔记可用（partial_notes.md）")
    if r.lingering_workers:
        parts.append(f"未退出线程: {r.lingering_workers}")
    if not r.success and not r.error and not r.partial_available:
        parts.append("未生成完整总结")
    return " | ".join(parts)


class CourseWindow(QMainWindow):
    # worker threads emit these; GUI thread consumes
    status_signal = pyqtSignal(str)
    started_signal = pyqtSignal()
    start_failed_signal = pyqtSignal(str)
    stopped_signal = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Course Session — 本地网课 AI 助手")
        self._session: CourseSession | None = None
        self._selector: RegionSelector | None = None
        self._stopping = False

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        top = QHBoxLayout()
        self.start_btn = QPushButton("开始课程 (选择画面区域)")
        self.start_btn.setStyleSheet(STYLE_START)
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn = QPushButton("结束课程")
        self.stop_btn.setStyleSheet(STYLE_STOP)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)
        top.addWidget(self.start_btn)
        top.addWidget(self.stop_btn)
        root.addLayout(top)

        self.info = QLabel(
            f"VLM: {VISION_MODEL} | 课后总结: {FINAL_MODEL} | "
            f"会话目录: {SESSIONS_DIR}\n"
            "结束方式：点「结束课程」，或运行 stop_course.ps1 写入 STOP_REQUEST。"
        )
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family:Consolas,monospace; font-size:12px;")
        root.addWidget(self.log, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪 — 点击「开始课程」框选网课播放区域")
        self.resize(860, 480)

        self.status_signal.connect(self._append_status, Qt.ConnectionType.QueuedConnection)
        self.started_signal.connect(self._on_started, Qt.ConnectionType.QueuedConnection)
        self.start_failed_signal.connect(self._on_start_failed, Qt.ConnectionType.QueuedConnection)
        self.stopped_signal.connect(self._on_stopped, Qt.ConnectionType.QueuedConnection)

    # ---------------- worker -> GUI callbacks (all on GUI thread) ------
    @pyqtSlot(str)
    def _append_status(self, msg: str) -> None:
        self.log.appendPlainText(msg)
        self.statusBar().showMessage(msg[:200])

    @pyqtSlot()
    def _on_started(self) -> None:
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage("课程进行中 — 结束时点击「结束课程」")

    @pyqtSlot(str)
    def _on_start_failed(self, err: str) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._session = None
        self.log.appendPlainText(f"启动失败: {err}")
        self.statusBar().showMessage("启动失败")

    @pyqtSlot(str)
    def _on_stopped(self, msg: str) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._stopping = False
        self._session = None
        self.log.appendPlainText(msg)
        if msg.startswith("课程已结束"):
            self.statusBar().showMessage("已结束")
        else:
            self.statusBar().showMessage("已结束（有错误/部分完成）")

    def _status_cb(self, msg: str) -> None:
        self.status_signal.emit(msg)

    # ---------------- start -------------------------------------------
    @pyqtSlot()
    def on_start(self) -> None:
        if self._session is not None or self._selector is not None:
            return
        self.statusBar().showMessage("请框选网课播放区域…（Esc 取消）")

        def on_done(region: Region | None) -> None:
            self._selector = None
            if region is None:
                self.statusBar().showMessage("已取消区域选择")
                return
            self.start_btn.setEnabled(False)
            self._session = CourseSession(
                region,
                on_status=self._status_cb,
                on_stop_requested=self._request_stop_from_flag,
            )
            threading.Thread(
                target=self._start_worker, daemon=True, name="SessionStart"
            ).start()

        self._selector = RegionSelector(on_done)
        self._selector.showFullScreen()

    def _start_worker(self) -> None:
        try:
            assert self._session is not None
            self._session.start()
            self.status_signal.emit(f"会话目录: {self._session.storage.dir}")
            self.started_signal.emit()
        except Exception as e:
            log.error("session start failed\n%s", traceback.format_exc())
            self.start_failed_signal.emit(str(e))

    # ---------------- stop --------------------------------------------
    def _request_stop_from_flag(self) -> None:
        """Called from session watcher thread when STOP_REQUEST file appears."""
        self.status_signal.emit("检测到 STOP_REQUEST，正在结束课程…")
        self._begin_stop()  # thread-safe: spawns its own worker

    @pyqtSlot()
    def on_stop(self) -> None:
        self._begin_stop()

    def _begin_stop(self) -> None:
        if self._session is None or self._stopping:
            return
        self._stopping = True
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        threading.Thread(
            target=self._stop_worker, daemon=True, name="SessionStop"
        ).start()

    def _stop_worker(self) -> None:
        try:
            assert self._session is not None
            result = self._session.stop(wait_final_summary=True)
            self.stopped_signal.emit(format_stop_result(result))
        except Exception as e:
            log.error("session stop failed\n%s", traceback.format_exc())
            self.stopped_signal.emit(f"结束时出错: {e}")

    # ---------------- close -------------------------------------------
    def closeEvent(self, e) -> None:
        try:
            if self._session is not None and self._session.running:
                r = self._session.stop(wait_final_summary=False)
                log.info("close stop result: %s", r)
        except Exception:
            pass
        super().closeEvent(e)


def main() -> int:
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("course-session")
    win = CourseWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
