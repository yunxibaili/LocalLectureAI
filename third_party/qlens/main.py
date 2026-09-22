import sys
import ctypes
import threading

# DPI-aware (per-monitor v2) BEFORE QApplication is created.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PyQt6.QtWidgets import QApplication  # noqa: E402

from config import HOTKEY  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _install_global_hotkey(window: MainWindow) -> None:
    """Best-effort global hotkey via `keyboard`. Falls back silently on failure."""
    try:
        import keyboard  # type: ignore
    except Exception:
        return

    def _fire():
        # Thread-safe: emit a queued signal into the GUI thread.
        window.request_capture.emit()

    def _listener():
        try:
            keyboard.add_hotkey(HOTKEY, _fire)
            keyboard.wait()
        except Exception:
            pass

    t = threading.Thread(target=_listener, daemon=True)
    t.start()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("qlens")
    window = MainWindow()
    window.show()
    _install_global_hotkey(window)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
