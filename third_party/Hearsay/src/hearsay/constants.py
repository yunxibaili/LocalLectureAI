"""Application constants and model configuration."""

APP_NAME = "Hearsay"
APP_VERSION = "1.1.5"
APP_AUTHOR = "Hearsay"

# Audio settings
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1  # Whisper expects mono
CHUNK_DURATION_S = 30  # Whisper's native context window
OVERLAP_DURATION_S = 1  # Overlap between chunks to prevent word splitting
AUDIO_DTYPE = "float32"

# Model table: name -> (parameters, vram_gb, english_only)
MODEL_TABLE = {
    "tiny": ("39M", 1, False),
    "tiny.en": ("39M", 1, True),
    "base": ("74M", 1, False),
    "base.en": ("74M", 1, True),
    "small": ("244M", 2, False),
    "small.en": ("244M", 2, True),
    "medium": ("769M", 5, False),
    "medium.en": ("769M", 5, True),
    "large-v3": ("1550M", 10, False),
    "turbo": ("809M", 6, False),
}

# Default model recommendations
DEFAULT_GPU_MODEL = "turbo"
DEFAULT_CPU_MODEL = "small.en"
DEFAULT_GPU_COMPUTE = "float16"
DEFAULT_CPU_COMPUTE = "int8"

# Audio source options
AUDIO_SOURCE_SYSTEM = "system"
AUDIO_SOURCE_MIC = "microphone"
AUDIO_SOURCE_BOTH = "both"

# Labels used in transcripts / live view for each source
SOURCE_LABELS = {
    AUDIO_SOURCE_SYSTEM: "Remote",
    AUDIO_SOURCE_MIC: "Local",
}

# Windows quieter than this RMS are treated as silence and not transcribed
SILENCE_RMS_FLOOR = 1e-4

# Silent-capture watchdog: if a session captures no audio (device muted,
# unplugged, blocked, or wedged) for this long, warn the user loudly but keep
# recording so it recovers if the device comes back. Re-warn periodically while
# still silent. A live microphone in a quiet room reads well above
# SILENCE_RMS_FLOOR, so these fire on genuinely dead capture, not normal pauses.
SILENCE_ALERT_S = 60      # seconds of no captured audio before the first alert
SILENCE_REALERT_S = 120   # re-alert interval while capture stays silent

# Tray icon colors (RGB)
ICON_COLOR_IDLE = (100, 100, 100)       # Gray
ICON_COLOR_RECORDING = (220, 50, 50)    # Red
ICON_COLOR_PROCESSING = (50, 150, 220)  # Blue

# Transcript formatting
PARAGRAPH_GAP_S = 2.0  # Silence gap (seconds) that triggers a paragraph break

# UI
LIVE_VIEW_POLL_MS = 250  # Poll transcript queue every 250ms
