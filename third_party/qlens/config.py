import os  # Course-app: model names must be configurable (documented upstream diff)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.environ.get("VISION_MODEL", "qwen2.5vl:7b")
TEXT_MODEL_NAME = os.environ.get("TEXT_MODEL_NAME", "qwen2.5:7b")  # for video-frame aggregation (text-only)
FALLBACK_VISION_MODEL = os.environ.get("FALLBACK_VISION_MODEL", "qwen3-vl:4b")
INFER_SIZE = 1024
# Aligned with app/course_session/settings.py REQUEST_TIMEOUT (env-overridable)
# so worker join timeouts derived from REQUEST_TIMEOUT cover both layers.
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "120"))
OVERLAY_AUTO_CLOSE_MS = 30_000
HOTKEY = "ctrl+shift+a"

# Defaults (overridable via Settings dialog at runtime)
MAX_OBJECTS_DEFAULT = 1
VIDEO_INTERVAL_S_DEFAULT = 0.0  # 0 = continuous (next frame grabbed as soon as previous one is processed)
VIDEO_MAX_FRAMES_DEFAULT = 60
