from dataclasses import dataclass
import numpy as np
import mss


@dataclass(frozen=True)
class Region:
    """Region in global virtual-desktop pixel coordinates."""
    x: int
    y: int
    w: int
    h: int


def grab(region: Region) -> np.ndarray:
    """Capture the screen region. Returns BGR ndarray (H, W, 3)."""
    with mss.mss() as sct:
        monitor = {"left": region.x, "top": region.y, "width": region.w, "height": region.h}
        raw = sct.grab(monitor)
        img = np.array(raw)  # BGRA
        return img[:, :, :3].copy()  # BGR
