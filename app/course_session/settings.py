"""Path bootstrap + runtime settings (env-overridable).

Puts third_party/qlens and third_party/Hearsay/src on sys.path so their
modules can be imported unmodified (adapter/reuse strategy).

Round 4 model roles (RTX 5070 12GB time-slicing):
  VISION_MODEL          -> realtime screen analysis (qwen3-vl:8b)
  REALTIME_FUSION_MODEL -> realtime note fusion (same resident 8B VLM)
  FINAL_MODEL           -> post-class exclusive (qwen38-27b-main) only after
                           realtime models are released and /api/ps verified.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
QLENS_DIR = ROOT / "third_party" / "qlens"
HEARSAY_SRC = ROOT / "third_party" / "Hearsay" / "src"
SESSIONS_DIR = ROOT / "sessions"

for p in (str(QLENS_DIR), str(HEARSAY_SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- Ollama / models ---
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434/api/chat")
VISION_MODEL = _env("VISION_MODEL", "qwen3-vl:8b")
FALLBACK_VISION_MODEL = _env("FALLBACK_VISION_MODEL", "qwen3-vl:4b")
# During class, realtime fusion must stay on the resident VLM (never 27B).
REALTIME_FUSION_MODEL = _env("REALTIME_FUSION_MODEL", VISION_MODEL)
# Post-class exclusive model. Final fusion + final summary only, and only
# after realtime models are released and /api/ps verified clean.
FINAL_MODEL = _env("FINAL_MODEL", "qwen38-27b-main:latest")
REQUEST_TIMEOUT = int(_env("REQUEST_TIMEOUT", "180"))
# Grace period added on top of REQUEST_TIMEOUT when joining worker threads
# before loading the exclusive post-class model (VRAM safety).
CLEANUP_GRACE = int(_env("CLEANUP_GRACE", "30"))
WORKER_JOIN_TIMEOUT = REQUEST_TIMEOUT + CLEANUP_GRACE
# Ollama context: thinking models burn ctx before answering; image+prompt need
# headroom (default 4096 truncated content -> empty replies). Verified locally.
NUM_CTX_LIVE = int(_env("NUM_CTX_LIVE", "8192"))
NUM_CTX_FINAL = int(_env("NUM_CTX_FINAL", "16384"))

# Retry/unload verification tuning
UNLOAD_RETRY_COUNT = int(_env("UNLOAD_RETRY_COUNT", "3"))
UNLOAD_RETRY_DELAY_S = float(_env("UNLOAD_RETRY_DELAY_S", "2.0"))


def ollama_base_url(ollama_url: str | None = None) -> str:
    """Scheme://host[:port] for any Ollama endpoint URL (never string-replace)."""
    raw = ollama_url if ollama_url is not None else OLLAMA_URL
    p = urlparse(raw)
    if not p.scheme or not p.netloc:
        raise ValueError(f"invalid OLLAMA_URL: {raw!r}")
    return f"{p.scheme}://{p.netloc}"


def validate_model_config() -> None:
    """Reject dangerous model-role configs before realtime start.

    Rules (fail closed):
    - VISION_MODEL / REALTIME_FUSION_MODEL must be non-empty.
    - Realtime roles must never be FINAL_MODEL or a 27B-class model.
    - REALTIME_FUSION_MODEL must not equal FINAL_MODEL.
    """
    realtime = {VISION_MODEL, REALTIME_FUSION_MODEL}
    for name in realtime:
        if not name:
            raise ValueError("VISION_MODEL / REALTIME_FUSION_MODEL must not be empty")
        if name == FINAL_MODEL or "27b" in name.lower():
            raise ValueError(
                f"invalid realtime model {name!r}: realtime phase must not use "
                f"{FINAL_MODEL!r} (27B is FINAL-only on 12GB)"
            )
    if REALTIME_FUSION_MODEL == FINAL_MODEL:
        raise ValueError(
            f"REALTIME_FUSION_MODEL must not equal FINAL_MODEL ({FINAL_MODEL!r})"
        )


def describe_model_roles() -> str:
    """Human-readable runtime model roles (printed at startup)."""
    return (
        f"VISION_MODEL={VISION_MODEL}\n"
        f"REALTIME_FUSION_MODEL={REALTIME_FUSION_MODEL}\n"
        f"FINAL_MODEL={FINAL_MODEL}"
    )


# --- Loaded-model registry (what this process actually used) ---
_model_reg_lock = threading.Lock()
_LOADED_MODELS: set[str] = set()


def mark_model_loaded(name: str) -> None:
    """Record a model name after a successful Ollama call that loaded it."""
    if not name:
        return
    with _model_reg_lock:
        _LOADED_MODELS.add(name)


def loaded_models() -> set[str]:
    with _model_reg_lock:
        return set(_LOADED_MODELS)


def forget_loaded_models(names: set[str] | None = None) -> None:
    with _model_reg_lock:
        if names is None:
            _LOADED_MODELS.clear()
        else:
            _LOADED_MODELS.difference_update(names)


# --- Ollama /api/ps queries (authoritative liveness check) ---
def list_loaded_models_via_api() -> set[str] | None:
    """Query Ollama /api/ps for models actually resident in VRAM.

    Returns set of resident model names (may be empty when nothing is
    loaded), or None when /api/ps cannot be queried (HTTP error,
    connection refused, timeout). Callers must treat None as
    "unknown / cannot verify", not as "nothing loaded".
    """
    try:
        import requests

        base = ollama_base_url()
        r = requests.get(f"{base}/api/ps", timeout=5)
        if r.status_code >= 400:
            log.warning("GET /api/ps returned HTTP %d", r.status_code)
            return None
        data = r.json()
        models = data.get("models", [])
        names: set[str] = set()
        for m in models:
            if isinstance(m, dict):
                # Ollama returns "name" or "model" depending on version
                n = m.get("name") or m.get("model") or ""
                if n:
                    names.add(n)
        return names
    except Exception as e:
        log.warning("GET /api/ps failed: %s", e)
        return None


def is_model_resident(name: str) -> bool | None:
    """Check if a specific model is resident via /api/ps.

    Returns True/False if verified, None if cannot query /api/ps.
    An empty successful /api/ps (no models loaded) returns False.
    """
    resident = list_loaded_models_via_api()
    if resident is None:
        return None  # cannot verify
    # exact match or prefix match (tag handling)
    for r in resident:
        if r == name or r.startswith(name + ":") or name.startswith(r + ":"):
            return True
    return False


def model_name_matches(a: str, b: str) -> bool:
    """True if two Ollama model name strings refer to the same model."""
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


# --- Cleanup result enum ---
class CleanupStatus:
    CLEANUP_OK = "CLEANUP_OK"
    CLEANUP_FAILED = "CLEANUP_FAILED"


# --- Visual stream ---
INFER_SIZE = int(_env("INFER_SIZE", "1024"))
# absdiff mean threshold (0-255) vs last ANALYZED frame to trigger VLM
FRAME_DIFF_HIGH = float(_env("FRAME_DIFF_HIGH", "6.0"))
# lower absdiff threshold (0-255) for slow writing (keep VLM rate low)
FRAME_DIFF_LOW = float(_env("FRAME_DIFF_LOW", "1.5"))
MIN_VLM_INTERVAL_S = float(_env("MIN_VLM_INTERVAL_S", "8"))
# if frame is above LOW this long without analysis, force one (freshness)
FORCE_REFRESH_S = float(_env("FORCE_REFRESH_S", "30"))
FRAME_POLL_S = float(_env("FRAME_POLL_S", "1.0"))

# --- Audio / Whisper (Hearsay) ---
WHISPER_MODEL = _env("WHISPER_MODEL", "turbo")      # multilingual; supports zh
WHISPER_DEVICE = _env("WHISPER_DEVICE", "auto")     # auto|cuda|cpu
WHISPER_COMPUTE = _env("WHISPER_COMPUTE", "")       # empty = by device
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "zh")
AUDIO_SOURCE = _env("AUDIO_SOURCE", "system")
# Round 10: observe-only instrumentation (default off; no behavior change).
INSTRUMENTATION = _env("INSTRUMENTATION", "false").strip().lower() in ("1", "true", "yes", "on")

# --- Course note fusion ---
FUSION_INTERVAL_S = float(_env("FUSION_INTERVAL_S", "45"))
RECENT_TRANSCRIPT_EVENTS = int(_env("RECENT_TRANSCRIPT_EVENTS", "40"))
RECENT_VISUAL_EVENTS = int(_env("RECENT_VISUAL_EVENTS", "6"))

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
# Active-session marker written by SessionStorage on start / cleared on stop.
ACTIVE_SESSION_MARKER = SESSIONS_DIR / "current_session.json"


def _ensure_cuda_dlls() -> None:
    """Make pip-installed NVIDIA runtime DLLs (cublas/cudnn) loadable.

    faster-whisper GPU (ctranslate2) needs cublas64_12.dll; without a full
    CUDA toolkit we ship the DLLs via pip packages and put them on PATH /
    the DLL search path before any whisper import happens.
    """
    try:
        import site
        from pathlib import Path as _P

        dirs = []
        for sp in site.getsitepackages() + [site.getusersitepackages()]:
            root = _P(sp) / "nvidia"
            if root.is_dir():
                for sub in root.iterdir():
                    for cand in ("bin", "lib"):
                        d = sub / cand
                        if d.is_dir():
                            dirs.append(str(d))
        if not dirs:
            return
        import os as _os
        cur = _os.environ.get("PATH", "")
        add = _os.pathsep.join(d for d in dirs if d not in cur.split(_os.pathsep))
        if add:
            _os.environ["PATH"] = add + _os.pathsep + cur
        for d in dirs:
            try:
                _os.add_dll_directory(d)
            except Exception:
                pass
    except Exception:
        pass


_ensure_cuda_dlls()
