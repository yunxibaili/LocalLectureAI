"""Temporary Round 10 instrumentation (observe-only).

Controlled by env INSTRUMENTATION=true|1|yes. When disabled (default),
every call is a no-op — no files, no I/O, no behavior change.

When enabled, appends one JSON object per line to a dedicated
instrumentation.jsonl (set_output / INSTRUMENTATION_PATH).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_enabled: bool | None = None
_path: Path | None = None
_seq = 0
_t0 = time.monotonic()


def enabled() -> bool:
    global _enabled
    if _enabled is None:
        raw = os.environ.get("INSTRUMENTATION", "false").strip().lower()
        _enabled = raw in ("1", "true", "yes", "on")
    return _enabled


def disable_for_tests() -> None:
    """Reset cached flag (unit tests only)."""
    global _enabled, _path, _seq, _t0
    _enabled = None
    _path = None
    _seq = 0
    _t0 = time.monotonic()


def set_output(path: str | Path) -> None:
    """Point the JSONL sink at *path* (parent created). No-op if disabled.

    Never raises — instrumentation must not break the host app.
    """
    global _path
    if not enabled():
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            _path = p
    except Exception:
        pass


def resolve_default_path() -> Path:
    env = os.environ.get("INSTRUMENTATION_PATH", "").strip()
    if env:
        return Path(env)
    from hearsay.utils.paths import get_log_dir

    return get_log_dir() / "instrumentation.jsonl"


def emit(event: str, **fields: Any) -> None:
    """Append one structured event. Safe no-op when instrumentation is off."""
    if not enabled():
        return
    global _seq
    rec: dict[str, Any] = {
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "mono_s": time.monotonic() - _t0,
        "event": event,
    }
    for k, v in fields.items():
        if v is not None:
            rec[k] = v
    try:
        with _lock:
            _seq += 1
            rec["seq"] = _seq
            path = _path if _path is not None else resolve_default_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Instrumentation must never break capture/transcription.
        pass
