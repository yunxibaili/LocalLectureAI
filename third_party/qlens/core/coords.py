from dataclasses import dataclass
from typing import List, Tuple
import numpy as np
import cv2

from core.capture import Region


@dataclass
class CoordinateMapper:
    """
    Deterministic transform chain:

        model_0_1000  ->  infer_px (e.g. 1024 letterboxed)
                       (x/1000 * infer_size)
        infer_px      ->  crop_px
                       ((p - pad) / scale)
        crop_px       ->  screen_px
                       (+ region.x, + region.y)

    Stores letterbox parameters from `prepare_image`.
    """
    region: Region
    infer_size: int
    scale: float = 1.0
    pad_x: int = 0
    pad_y: int = 0
    crop_w: int = 0
    crop_h: int = 0

    # ---------- single-axis helpers ----------
    def _model_to_infer(self, v: float) -> float:
        return float(v) / 1000.0 * self.infer_size

    def _infer_to_crop_x(self, ix: float) -> float:
        return (ix - self.pad_x) / self.scale

    def _infer_to_crop_y(self, iy: float) -> float:
        return (iy - self.pad_y) / self.scale

    # ---------- public API ----------
    def model_to_crop(self, x_1000: float, y_1000: float) -> Tuple[float, float]:
        ix = self._model_to_infer(x_1000)
        iy = self._model_to_infer(y_1000)
        cx = self._infer_to_crop_x(ix)
        cy = self._infer_to_crop_y(iy)
        cx = float(np.clip(cx, 0, self.crop_w - 1))
        cy = float(np.clip(cy, 0, self.crop_h - 1))
        return cx, cy

    def model_to_screen(self, x_1000: float, y_1000: float) -> Tuple[int, int]:
        cx, cy = self.model_to_crop(x_1000, y_1000)
        return int(round(cx + self.region.x)), int(round(cy + self.region.y))

    def bbox_model_to_crop(self, bbox: List[float]) -> List[int]:
        x1, y1, x2, y2 = bbox
        cx1, cy1 = self.model_to_crop(x1, y1)
        cx2, cy2 = self.model_to_crop(x2, y2)
        if cx2 < cx1:
            cx1, cx2 = cx2, cx1
        if cy2 < cy1:
            cy1, cy2 = cy2, cy1
        return [int(round(cx1)), int(round(cy1)), int(round(cx2)), int(round(cy2))]

    def bbox_model_to_screen(self, bbox: List[float]) -> List[int]:
        c = self.bbox_model_to_crop(bbox)
        return [c[0] + self.region.x, c[1] + self.region.y,
                c[2] + self.region.x, c[3] + self.region.y]


def prepare_image(img_bgr: np.ndarray, region: Region, infer_size: int) -> Tuple[np.ndarray, CoordinateMapper]:
    """
    Letterbox-resize the crop into `infer_size x infer_size` (black padding),
    preserving aspect ratio. Returns (resized_image, mapper).
    """
    h, w = img_bgr.shape[:2]
    scale = min(infer_size / w, infer_size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((infer_size, infer_size, 3), dtype=np.uint8)
    pad_x = (infer_size - new_w) // 2
    pad_y = (infer_size - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

    mapper = CoordinateMapper(
        region=region,
        infer_size=infer_size,
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
        crop_w=w,
        crop_h=h,
    )
    return canvas, mapper
