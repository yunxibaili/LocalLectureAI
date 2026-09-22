import logging
import sys
import time

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.course_session.visual import VisualStream  # noqa: E402
from core.capture import Region  # noqa: E402

events = []


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
    f"err={vs.last_error!r} status={vs.last_status!r}",
    flush=True,
)
print("events:", len(events))
for e in events:
    print(f"  f={e.frame_id} diff={e.diff_score} len={len(e.description)}")
