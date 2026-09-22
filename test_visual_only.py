import logging
import sys
import time

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.course_session.visual import VisualStream  # noqa: E402
from core.capture import Region  # noqa: E402

events = []
errors = []


def on_vis(e):
    events.append(e)
    print(f"  [vis frame={e.frame_id} t={e.session_t:.1f}s diff={e.diff_score}] "
          f"{e.description[:100]}", flush=True)


region = Region(400, 300, 400, 300)
vs = VisualStream(region=region, on_event=on_vis)
vs.start()
time.sleep(120)
vs.stop()
vs.join(timeout=90)
print(
    f"frames={vs.frame_id} vlm_calls={vs.vlm_calls} skipped={vs.skipped_frames} "
    f"failed={vs.failed_analyses} err={vs.last_error!r} status={vs.last_status!r} "
    f"alive={vs.is_alive()}",
    flush=True,
)
print("events:", len(events))
for e in events:
    print(f"  f={e.frame_id} diff={e.diff_score} len={len(e.description)}")

# P2-6: must exit non-zero on failure (never silent success)
checks = {
    "thread_stopped": not vs.is_alive(),
    "frames_advanced": vs.frame_id >= 5,
    "got_at_least_one_event": len(events) >= 1,
    "event_description_nonempty": all((e.description or "").strip() for e in events) if events else False,
    "no_hard_error": vs.last_error == "" or "VLM" not in vs.last_error,
}
print("=" * 60, flush=True)
for k, v in checks.items():
    print(f"  {'OK ' if v else 'FAIL'} {k}", flush=True)
overall = all(checks.values())
print(f"VISUAL_ONLY_TEST {'PASS' if overall else 'FAIL'}", flush=True)
sys.exit(0 if overall else 1)
