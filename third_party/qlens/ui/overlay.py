from typing import List, Dict, Any, Optional

from PyQt6.QtCore import Qt, QRect, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QGuiApplication
from PyQt6.QtWidgets import QWidget

from config import OVERLAY_AUTO_CLOSE_MS
from render.draw import color_for


def _bgr_to_qcolor(bgr) -> QColor:
    b, g, r = bgr
    return QColor(r, g, b)


class Overlay(QWidget):
    """Frameless click-through overlay spanning the virtual desktop."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._virt_rect = self._virtual_desktop_rect()
        self.setGeometry(self._virt_rect)
        self._bbox: Optional[List[int]] = None
        self._bbox_label: str = ""
        self._multi: List[Dict[str, Any]] = []  # [{bbox:[..], label:str, color:QColor}]
        self._points: List[Dict[str, Any]] = []
        self._points_label: str = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)

    @staticmethod
    def _virtual_desktop_rect() -> QRect:
        rect = QRect()
        for s in QGuiApplication.screens():
            rect = rect.united(s.geometry())
        return rect

    def show_bbox(self, bbox_screen_px: List[int], label: str) -> None:
        self._bbox = bbox_screen_px
        self._bbox_label = label
        self._multi = []
        self._points = []
        self.show()
        self._timer.start(OVERLAY_AUTO_CLOSE_MS)
        self.update()

    def show_multi_bbox(self, items: List[Dict[str, Any]]) -> None:
        """items: [{bbox_screen_px:[..], label:str}, ...]"""
        self._multi = [
            {"bbox": it["bbox_screen_px"], "label": it.get("label", ""),
             "color": _bgr_to_qcolor(color_for(i))}
            for i, it in enumerate(items)
        ]
        self._bbox = None
        self._points = []
        self.show()
        self._timer.start(OVERLAY_AUTO_CLOSE_MS)
        self.update()

    def show_points(self, points_screen_px: List[Dict[str, Any]], label: str) -> None:
        self._points = points_screen_px
        self._points_label = label
        self._bbox = None
        self._multi = []
        self.show()
        self._timer.start(OVERLAY_AUTO_CLOSE_MS)
        self.update()

    def clear(self) -> None:
        self._bbox = None
        self._multi = []
        self._points = []
        self.hide()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ox, oy = self._virt_rect.x(), self._virt_rect.y()
        screen = self.screen() or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        def to_logical(px_x, px_y):
            return px_x / dpr - ox, px_y / dpr - oy

        def draw_rect_with_label(x1, y1, x2, y2, label, color: QColor):
            lx1, ly1 = to_logical(x1, y1)
            lx2, ly2 = to_logical(x2, y2)
            p.setPen(QPen(color, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(int(lx1), int(ly1), int(lx2 - lx1), int(ly2 - ly1))
            if label:
                p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                tx, ty = int(lx1), max(int(ly1) - 6, 14)
                p.fillRect(tx, ty - 16, p.fontMetrics().horizontalAdvance(label) + 10, 20, color)
                p.setPen(QColor(255, 255, 255))
                p.drawText(tx + 5, ty - 2, label)

        if self._bbox:
            x1, y1, x2, y2 = self._bbox
            draw_rect_with_label(x1, y1, x2, y2, self._bbox_label, QColor(0, 220, 0))

        for it in self._multi:
            x1, y1, x2, y2 = it["bbox"]
            draw_rect_with_label(x1, y1, x2, y2, it["label"], it["color"])

        if self._points:
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            for inst in self._points:
                sx, sy = inst["point_px"]
                cx, cy = to_logical(sx, sy)
                cx, cy = int(cx), int(cy)
                p.setPen(QPen(QColor(255, 255, 255), 2))
                p.setBrush(QColor(230, 0, 0))
                p.drawEllipse(cx - 9, cy - 9, 18, 18)
                p.setPen(QColor(255, 255, 255))
                p.drawText(cx + 12, cy + 5, str(inst.get("id", 0)))
            if self._points_label:
                txt = f"{self._points_label}: {len(self._points)}"
                p.setPen(QColor(255, 255, 255))
                p.fillRect(10, 10, p.fontMetrics().horizontalAdvance(txt) + 14, 24, QColor(230, 0, 0))
                p.drawText(17, 28, txt)
