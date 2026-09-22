from typing import Callable, Optional

from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QGuiApplication, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QWidget

from core.capture import Region


class RegionSelector(QWidget):
    """Snipping-Tool-like fullscreen selector spanning the virtual desktop."""

    def __init__(self, on_done: Callable[[Optional[Region]], None]):
        super().__init__(None)
        self._on_done = on_done
        self._start: Optional[QPoint] = None
        self._end: Optional[QPoint] = None
        self._virt_rect: QRect = self._virtual_desktop_rect()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(self._virt_rect)

    @staticmethod
    def _virtual_desktop_rect() -> QRect:
        screens = QGuiApplication.screens()
        rect = QRect()
        for s in screens:
            rect = rect.united(s.geometry())
        return rect

    # ---------- events ----------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self._start and self._end:
            r = QRect(self._start, self._end).normalized()
            # clear the selection rectangle
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(r, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(QColor(255, 60, 60), 2)
            p.setPen(pen)
            p.drawRect(r)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._start = e.position().toPoint()
            self._end = self._start
            self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._start is not None:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self._end = e.position().toPoint()
        local = QRect(self._start, self._end).normalized()
        # Qt gives logical pixels; mss needs physical pixels. Convert via DPR.
        screen = self.screen() or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else self.devicePixelRatioF()
        gx_logical = local.x() + self._virt_rect.x()
        gy_logical = local.y() + self._virt_rect.y()
        gx = int(round(gx_logical * dpr))
        gy = int(round(gy_logical * dpr))
        w = max(1, int(round(local.width() * dpr)))
        h = max(1, int(round(local.height() * dpr)))
        region = Region(gx, gy, w, h)
        self.close()
        cb = self._on_done
        self._on_done = lambda _r: None
        if region.w < 10 or region.h < 10:
            cb(None)
        else:
            cb(region)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            cb = self._on_done
            self._on_done = lambda _r: None
            cb(None)
