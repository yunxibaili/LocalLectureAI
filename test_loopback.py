# WASAPI loopback + AudioBridge end-to-end Chinese transcription test
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.path.insert(0, ".")

from app.course_session.audio_bridge import AudioBridge  # noqa: E402
from app.course_session.settings import SESSIONS_DIR  # noqa: E402

events = []
fatals = []
no_audio_flags = []


def on_event(ev):
    events.append(ev)
    print(f"  [event t={ev.session_t:.1f}s] {ev.text}", flush=True)


test_dir = SESSIONS_DIR / f"_loopback_test_{int(time.time())}"
test_dir.mkdir(parents=True, exist_ok=True)

bridge = AudioBridge(
    on_event=on_event,
    transcript_dir=test_dir,
    on_fatal=lambda e: (fatals.append(e), print(f"FATAL: {e}", flush=True)),
    on_no_audio=lambda: (no_audio_flags.append(1), print("NO_AUDIO alert", flush=True)),
)

t_start = time.monotonic()
print("starting AudioBridge (whisper load + WASAPI loopback)...", flush=True)
bridge.start()
print(f"started in {time.monotonic() - t_start:.1f}s status={bridge.last_status}", flush=True)

# Run long enough for one full 30s window + margin
deadline = time.monotonic() + 48
while time.monotonic() < deadline:
    time.sleep(1)
    if bridge.chunks_transcribed >= 1 and time.monotonic() - t_start > 35:
        break

print(f"stopping... chunks={bridge.chunks_transcribed} events={bridge.events_emitted}", flush=True)
t_stop = time.monotonic()
bridge.stop()
print(f"stopped in {time.monotonic() - t_stop:.1f}s", flush=True)

print("=" * 60)
print(f"events_emitted={bridge.events_emitted} chunks={bridge.chunks_transcribed}")
print(f"fatals={fatals} no_audio_alerts={len(no_audio_flags)}")
print(f"last_error={bridge.last_error!r}")
print(f"writer={bridge.writer_path} exists={bridge.writer_path.exists()}")
if bridge.writer_path.exists():
    print("--- transcript head ---")
    print(bridge.writer_path.read_text(encoding="utf-8")[:800])
print(f"session dir: {test_dir}")
for p in sorted(test_dir.rglob("*")):
    print(" ", p.relative_to(test_dir))
ok = bridge.events_emitted > 0 and not fatals
print("LOOPBACK_TEST", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
