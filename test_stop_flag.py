# STOP_REQUEST flag path: flag -> _flag_loop -> on_stop_requested -> stop(+summary)
import os
import sys
import threading
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FUSION_INTERVAL_S", "30")
sys.path.insert(0, ".")

from app.course_session.session import CourseSession  # noqa: E402
from core.capture import Region  # noqa: E402

region = Region(140, 175, 800, 430)
statuses = []
stop_started = threading.Event()


def on_status(msg):
    statuses.append(msg)
    print(f"STATUS {time.strftime('%H:%M:%S')} {msg}", flush=True)


sess = CourseSession(region, on_status=on_status)


def on_flag():
    print("FLAG detected by _flag_loop -> spawning stop", flush=True)
    stop_started.set()

    def _run():
        sess.stop(wait_final_summary=True)

    threading.Thread(target=_run, daemon=True, name="FlagStop").start()


sess._on_stop_requested = on_flag
sess.start()
print(f"started dir={sess.storage.dir}", flush=True)

# Live long enough for one audio chunk (>35s) so stop has real data
time.sleep(50)
flag = sess.storage.dir / "STOP_REQUEST"
flag.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
print(f"STOP_REQUEST written: {flag}", flush=True)

deadline = time.monotonic() + 420
while time.monotonic() < deadline:
    # running flips False before final_summary finishes (stop() sets it early);
    # wait for the summary file itself when wait_final_summary=True.
    if not sess.running and (sess.storage.dir / "final_summary.md").exists():
        break
    time.sleep(3)
# small grace for file flush
time.sleep(2)

d = sess.storage.dir
checks = {
    "flag_seen": stop_started.is_set(),
    "stopped": not sess.running,
    "transcript.md": (d / "transcript.md").exists() and (d / "transcript.md").stat().st_size > 50,
    "final_summary.md": (d / "final_summary.md").exists() and (d / "final_summary.md").stat().st_size > 300,
    "live_notes.md": (d / "live_notes.md").exists(),
    "events.jsonl": (d / "events.jsonl").exists() and (d / "events.jsonl").stat().st_size > 0,
}
print("=" * 60, flush=True)
for k, v in checks.items():
    print(f"  {'OK ' if v else 'FAIL'} {k}", flush=True)
fs = d / "final_summary.md"
if fs.exists():
    print("--- final_summary head ---", flush=True)
    print(fs.read_text(encoding="utf-8")[:400], flush=True)
overall = all(checks.values())
print(f"STOP_FLAG_TEST {'PASS' if overall else 'FAIL'}", flush=True)
sys.exit(0 if overall else 1)
