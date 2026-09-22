"""Manual device check — REQUIRES speakers + microphone and ~3 minutes.

    python scripts/manual_device_check.py

Plays Windows TTS through the default output device while recording, so
run it only when no real Hearsay session is active (it contends for the
same audio devices) and audible synthesized speech is acceptable.

Covers: WASAPI loopback capture in callback mode, sub-second stop, rapid
stop->start cycling (the historical device-busy failure), and a full
record -> transcribe -> markdown pass with source labels. The Whisper
model (small.en) is downloaded on first use if not already cached.
"""
import queue
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hearsay.audio.recorder as rec_mod
from hearsay.audio.recorder import AudioRecorder
from hearsay.constants import AUDIO_SOURCE_BOTH, AUDIO_SOURCE_SYSTEM

OUT = Path(tempfile.mkdtemp(prefix="hearsay_devcheck_"))

FAILURES = []


def check(cond, msg):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {msg}", flush=True)
    if not cond:
        FAILURES.append(msg)


def speak(text, rate=1):
    """Speak via SAPI through the default output device (async)."""
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -AssemblyName System.Speech; "
         "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
         f"$s.Rate = {rate}; $s.Speak('{text}')"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


fatal_calls = []


def on_fatal(exc):
    fatal_calls.append(exc)
    print(f"  !! on_fatal: {exc}", flush=True)


# ---------------------------------------------------------------
print("== Test 1: Both-mode callback capture (10s windows) ==", flush=True)
rec_mod.CHUNK_DURATION_S = 10  # shorten windows for the test

q1 = queue.Queue(maxsize=10)
r1 = AudioRecorder(audio_queue=q1, source=AUDIO_SOURCE_BOTH, on_fatal=on_fatal)
r1.start()
time.sleep(1.0)
speak("This is a capture test of the system audio loopback stream.")
time.sleep(12.0)

t0 = time.perf_counter()
r1.stop()
r1.join(timeout=10)
stop_secs = time.perf_counter() - t0
check(not r1.is_alive(), "recorder thread exited")
check(stop_secs < 3.0, f"stop was fast ({stop_secs:.2f}s)")

chunks = []
while not q1.empty():
    chunks.append(q1.get_nowait())
check(len(chunks) >= 1, f"captured {len(chunks)} window(s)")
sources = {s for c in chunks for s in c.parts}
print(f"  sources captured: {sources}", flush=True)
check(AUDIO_SOURCE_SYSTEM in sources, "system (loopback) audio captured via callback mode")
for c in chunks:
    print(f"  window {c.index}: start={c.window_start:.1f}s "
          + ", ".join(f"{s}={len(a)/16000:.1f}s" for s, a in c.parts.items()), flush=True)
check(not fatal_calls, "no fatal errors")

# ---------------------------------------------------------------
print("== Test 2: rapid stop->start x3 (device-busy scenario) ==", flush=True)
for i in range(3):
    q = queue.Queue(maxsize=10)
    r = AudioRecorder(audio_queue=q, source=AUDIO_SOURCE_BOTH, on_fatal=on_fatal)
    r.start()
    speak(f"Rapid cycle number {i + 1} is now recording.")
    time.sleep(6.0)
    r.stop()
    # Deliberately do NOT join before starting the next cycle (old bug path);
    # the retry-on-open logic must absorb any device-still-held window, and
    # failures after a user-requested stop must stay silent (no on_fatal).
    if i == 2:
        r.join(timeout=10)
    print(f"  cycle {i + 1} done", flush=True)
time.sleep(3.0)
check(not fatal_calls, f"no fatal errors across rapid cycles ({len(fatal_calls)})")

# ---------------------------------------------------------------
print("== Test 3: end-to-end with real Whisper (30s windows) ==", flush=True)
rec_mod.CHUNK_DURATION_S = 30

from hearsay.output.markdown_writer import MarkdownWriter
from hearsay.transcription.engine import TranscriptionEngine
from hearsay.transcription.pipeline import TranscriptionPipeline

engine = TranscriptionEngine(model_name="small.en", device="cpu",
                             compute_type="int8", language="en", vad_filter=True)
print("  loading model...", flush=True)
engine.load()

aq = queue.Queue(maxsize=10)
tq = queue.Queue()
pipe = TranscriptionPipeline(audio_queue=aq, transcript_queue=tq, engine=engine)
pipe.start()
rec = AudioRecorder(audio_queue=aq, source=AUDIO_SOURCE_BOTH, on_fatal=on_fatal)
rec.start()

speak("Good morning everyone. This is the remote speaker coming through the "
      "system audio channel. The quick brown fox jumps over the lazy dog.")
time.sleep(35)
speak("And now a second remote statement arrives in the following window to "
      "prove that transcription continues across chunk boundaries.")
time.sleep(35)

rec.stop()
rec.join(timeout=10)
check(not rec.is_alive(), "recorder exited cleanly")
pipe.stop()
pipe.join(timeout=300)
check(not pipe.is_alive(), "pipeline drained and exited")
engine.unload()

writer = MarkdownWriter(OUT, title="Device Check E2E")
results = []
while not tq.empty():
    results.append(tq.get_nowait())
for r in results:
    writer.append(r)
writer.finalize(total_duration=72)
writer.post_process()
content = writer.file_path.read_text(encoding="utf-8")
print("---- transcript ----", flush=True)
print(content, flush=True)
print("--------------------", flush=True)
check(len(results) >= 1, f"{len(results)} transcription result(s)")
check("**Remote:**" in content, "Remote label present")
check("brown fox" in content.lower(), "first TTS sentence transcribed")
check("second remote statement" in content.lower(), "second window transcribed")
check(not fatal_calls, "no fatal errors in E2E")
print(f"  transcript file: {writer.file_path}", flush=True)

print(flush=True)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
