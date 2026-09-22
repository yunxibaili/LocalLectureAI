# End-to-end CourseSession test: audio + visual + fusion + final summary.
import json
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
stop_result = sess.stop(wait_final_summary=True)
t_done = time.monotonic() - t_stop
print(f"stop completed in {t_done:.1f}s result={stop_result}", flush=True)

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
checks["stop_result_success"] = bool(stop_result.success)
checks["stop_state_stopped"] = stop_result.state == "stopped"
checks["no_lingering_workers"] = not stop_result.lingering_workers

# content assertions (P2-6)
if checks["events.jsonl"]:
    lines = [ln for ln in (d / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    ok_ev = False
    for ln in lines:
        try:
            o = json.loads(ln)
            if (o.get("text") or "").strip():
                ok_ev = True
                break
        except Exception:
            continue
    checks["events_nonempty_text"] = ok_ev

if checks["live_notes.md"]:
    ln = (d / "live_notes.md").read_text(encoding="utf-8")
    checks["live_notes_has_header"] = ln.lstrip().startswith("#") and not ln.lstrip().startswith("##")
    checks["live_notes_has_section"] = "##" in ln

if checks["transcript.md"]:
    tr = (d / "transcript.md").read_text(encoding="utf-8")
    checks["transcript_has_cjk"] = any("一" <= c <= "鿿" for c in tr)

if checks["final_summary.md"]:
    fs_text = fs.read_text(encoding="utf-8")
    checks["final_summary_has_course_theme"] = ("课程主题" in fs_text) or ("知识体系" in fs_text)
    checks["final_summary_not_placeholder"] = "无法生成总结" not in fs_text
    checks["final_summary_generated_by_model"] = "qwen38" in fs_text or "generated" in fs_text.lower()

snap = sess.snapshot()
print("=" * 60, flush=True)
print(f"session dir: {d}", flush=True)
print(f"snap: tr={snap['transcripts']} vis={snap['visual_events']} vlm={snap['vlm_calls']} "
      f"skip={snap['vlm_skipped']} upd={snap['updates']} topic={snap['topic']!r} "
      f"state={snap['state']} workers={snap['workers_alive']}", flush=True)
for name, ok in checks.items():
    p = d / name if (d / name).exists() else None
    size = p.stat().st_size if p else 0
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" ({size} bytes)" if p else ""), flush=True)

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
