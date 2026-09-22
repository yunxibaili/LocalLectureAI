from typing import List, Dict, Any, Tuple
import cv2
import numpy as np

GREEN = (0, 220, 0)
RED = (0, 0, 230)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# BGR palette for multi-bbox
PALETTE: List[Tuple[int, int, int]] = [
    (0, 220, 0),     # green
    (0, 180, 255),   # orange
    (255, 220, 0),   # cyan
    (255, 80, 200),  # pink
    (60, 100, 255),  # red-orange
    (180, 100, 255), # magenta
    (0, 255, 255),   # yellow
    (255, 255, 0),   # cyan-bright
    (200, 200, 0),   # teal
    (100, 200, 50),  # lime
]


def color_for(i: int) -> Tuple[int, int, int]:
    return PALETTE[i % len(PALETTE)]


def _label(img: np.ndarray, text: str, org: tuple, bg=GREEN, fg=WHITE) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.6
    th = 1
    (tw, hh), bl = cv2.getTextSize(text, font, scale, th)
    x, y = org
    cv2.rectangle(img, (x, y - hh - bl - 4), (x + tw + 6, y), bg, -1)
    cv2.putText(img, text, (x + 3, y - bl - 2), font, scale, fg, th, cv2.LINE_AA)


def draw_bbox(img: np.ndarray, bbox_px: List[int], label: str) -> np.ndarray:
    out = img.copy()
    x1, y1, x2, y2 = bbox_px
    cv2.rectangle(out, (x1, y1), (x2, y2), GREEN, 3)
    if label:
        _label(out, label, (x1, max(y1, 18)))
    return out


def draw_multi_bbox(img: np.ndarray, items: List[Dict[str, Any]]) -> np.ndarray:
    """items: [{bbox_px:[x1,y1,x2,y2], label:str}, ...]"""
    out = img.copy()
    for i, it in enumerate(items):
        color = color_for(i)
        x1, y1, x2, y2 = it["bbox_px"]
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3)
        lbl = it.get("label", "")
        if lbl:
            _label(out, lbl, (x1, max(y1, 18)), bg=color)
    return out


def draw_points(img: np.ndarray, points_px: List[Dict[str, Any]], label: str = "") -> np.ndarray:
    out = img.copy()
    for inst in points_px:
        x, y = inst["point_px"]
        idx = inst.get("id", 0)
        cv2.circle(out, (int(x), int(y)), 9, RED, -1)
        cv2.circle(out, (int(x), int(y)), 11, WHITE, 2)
        cv2.putText(out, str(idx), (int(x) + 12, int(y) + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 2, cv2.LINE_AA)
        cv2.putText(out, str(idx), (int(x) + 12, int(y) + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, BLACK, 1, cv2.LINE_AA)
    if label and points_px:
        _label(out, f"{label}: {len(points_px)}", (10, 28), bg=RED)
    return out
