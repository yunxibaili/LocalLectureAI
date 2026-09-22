"""Apply the Hearsay logo to a window's title bar."""

from __future__ import annotations

import logging
from typing import Any

from hearsay.utils.paths import get_asset_path

log = logging.getLogger(__name__)


def apply_window_icon(window: Any) -> None:
    """Set the Hearsay ``.ico`` on a Tk/CustomTkinter window's title bar.

    customtkinter 5.2.x sets its own icon roughly 200ms after a CTk/CTkToplevel
    is created, so the icon is applied immediately *and* re-applied on a short
    delay to survive that reset. All failures are non-fatal — a missing icon is
    cosmetic and must never break a window.
    """
    icon = get_asset_path("icon.ico")
    if not icon.exists():
        log.debug("Window icon not found at %s", icon)
        return
    path = str(icon)

    def _set() -> None:
        try:
            window.iconbitmap(path)
        except Exception:
            log.debug("iconbitmap failed", exc_info=True)

    _set()
    try:
        window.after(300, _set)
    except Exception:
        log.debug("Could not schedule icon re-apply", exc_info=True)
