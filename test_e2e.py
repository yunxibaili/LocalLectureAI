# End-to-end CourseSession test: audio + visual + fusion + final summary.
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FUSION_INTERVAL_S", "30")
os.environ.setdefault("REQUEST_TIMEOUT", "300")
sys.path.insert(0, ".")

from app.course_session.session import CourseSession  # noqa: E402
from core.capture import Region  # noqa: E402

# Slide window: geometry 860x520+120+120 (slides_tk.py) -> client area inside.
region = Region(140, 175, 800, 430)

statuses = []


def on_status(msg):
    statuses.append(msg)
    print(f"STATUS {time.strftime('%H:%M:%S')} {msg}", flush=True)


print(f"E2E start region={region}", flush=True)
sess = CourseSession(region, on_status=on_status)
t_start = time.monotonic()
sess.start()
print(f"started in {time.monotonic()-t_start:.1f}s dir={sess.storage.dir}", flush=True)

# Live phase: >30s (first audio chunk) + visual first call + 2 fusion rounds
LIVE_S = 100
while time.monotonic() - t_start < LIVE_S:
    time.sleep(5)
    snap = sess.snapshot()
    print(f"SNAP t={time.monotonic()-t_start:.0f}s tr={snap['transcripts']} "
          f"vis={snap['visual_events']} vlm={snap['vlm_calls']} skip={snap['vlm_skipped']} "
          f"upd={snap['updates']} topic={snap['topic']!r} audio={snap['audio'][:60]!r}",
          flush=True)

print("stopping (incl. final fusion + final_summary)...", flush=True)
t_stop = time.monotonic()
sess.stop(wait_final_summary=True)
t_done = time.monotonic() - t_stop
print(f"stop completed in {t_done:.1f}s", flush=True)

# ---------------- verify artifacts ----------------
d = sess.storage.dir
checks = {}
checks["events.jsonl"] = (d / "events.jsonl").exists() and (d / "events.jsonl").stat().st_size > 0
checks["visual_events.jsonl"] = (d / "visual_events.jsonl").exists() and (d / "visual_events.jsonl").stat().st_size > 0
checks["course_state.jsonl"] = (d / "course_state.jsonl").exists() and (d / "course_state.jsonl").stat().st_size > 0
checks["live_notes.md"] = (d / "live_notes.md").exists() and (d / "live_notes.md").stat().st_size > 200
checks["transcript.md"] = (d / "transcript.md").exists() and (d / "transcript.md").stat().st_size > 100
fs = d / "final_summary.md"
checks["final_summary.md"] = fs.exists() and fs.stat().st_size > 500

snap = sess.snapshot()
print("=" * 60, flush=True)
print(f"session dir: {d}", flush=True)
print(f"snap: tr={snap['transcripts']} vis={snap['visual_events']} vlm={snap['vlm_calls']} "
      f"skip={snap['vlm_skipped']} upd={snap['updates']} topic={snap['topic']!r}", flush=True)
for name, ok in checks.items():
    p = d / name
    size = p.stat().st_size if p.exists() else 0
    print(f"  {'OK ' if ok else 'FAIL'} {name} ({size} bytes)", flush=True)

# content spot-checks
if checks["transcript.md"]:
    tr = (d / "transcript.md").read_text(encoding="utf-8")
    print("--- transcript head ---", flush=True)
    print(tr[:400], flush=True)
if checks["live_notes.md"]:
    ln = (d / "live_notes.md").read_text(encoding="utf-8")
    print("--- live_notes head ---", flush=True)
    print(ln[:400], flush=True)
if checks["final_summary.md"]:
    print("--- final_summary head ---", flush=True)
    print(fs.read_text(encoding="utf-8")[:600], flush=True)

overall = all(checks.values()) and snap["transcripts"] >= 1 and snap["updates"] >= 1
print(f"E2E_TEST {'PASS' if overall else 'FAIL'}", flush=True)
print(f"statuses_count={len(statuses)}", flush=True)
sys.exit(0 if overall else 1)
