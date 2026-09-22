# Fusion + visual-gate test using NoteEngine + VisualStream (real Ollama calls)
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.path.insert(0, ".")

from app.course_session.events import TranscriptEvent, VisualEvent  # noqa: E402
from app.course_session.note_engine import NoteEngine  # noqa: E402
from app.course_session.settings import SESSIONS_DIR  # noqa: E402
from app.course_session.storage import SessionStorage  # noqa: E402
from app.course_session.visual import VisualStream  # noqa: E402
from core.capture import Region  # noqa: E402

results = {}

# ---------------- fusion test ----------------
print("=" * 60, flush=True)
print("FUSION TEST", flush=True)
sess = SESSIONS_DIR / f"_fusion_test_{int(time.time())}"
sess.mkdir(parents=True, exist_ok=True)
storage = SessionStorage(sess)
ne = NoteEngine(storage)

trs = [
    TranscriptEvent(timestamp=time.time(), duration=3, text="同学们好，今天我们讲二次函数的图像与性质。", source="system_audio", session_t=5.0),
    TranscriptEvent(timestamp=time.time(), duration=4, text="判别式等于b的平方减4ac，a不能等于0。", source="system_audio", session_t=12.0),
]
vis = [
    VisualEvent(timestamp=time.time(), frame_id=1, description="黑板上写着 y=ax^2+bx+c，坐标系开口向上。", changed=True, session_t=8.0, diff_score=20.5),
]

t0 = time.monotonic()
updated = ne.fuse(trs, vis)
t_fuse = time.monotonic() - t0
print(f"fuse1 ok={updated} in {t_fuse:.1f}s topic={ne.state.current_topic!r}", flush=True)
print(f"  concepts={ne.state.current_concepts}", flush=True)
print(f"  formulas={ne.state.formulas}", flush=True)
print(f"  emphasis={ne.state.teacher_emphasis}", flush=True)

t1 = time.monotonic()
updated2 = ne.fuse(trs, vis)
t_fuse2 = time.monotonic() - t1
print(f"fuse2 ok={updated2} in {t_fuse2:.1f}s updates={ne.state.updates}", flush=True)

live = storage.live_notes_path
statef = storage.course_state_path
print(f"live_notes exists={live.exists()} size={live.stat().st_size if live.exists() else 0}", flush=True)
print(f"course_state exists={statef.exists()} lines={len(statef.read_text(encoding='utf-8').splitlines()) if statef.exists() else 0}", flush=True)

results["fusion"] = updated and len(ne.state.current_concepts) > 0
results["fusion_s"] = round(t_fuse, 1)
results["fusion_files"] = live.exists() and statef.exists()

# ---------------- visual gate test ----------------
print("=" * 60, flush=True)
print("VISUAL GATE TEST (static region, 20s)", flush=True)
vis_events = []


def on_vis(e):
    vis_events.append(e)
    print(f"  [vis frame={e.frame_id} diff={e.diff_score}] {e.description[:90]}", flush=True)


region = Region(400, 300, 400, 300)
vs = VisualStream(region=region, on_event=on_vis)
vs.start()
# First VLM call with thinking models takes ~40-70s; give it time, then
# collect static frames that must be skipped.
time.sleep(100)
vs.stop()
vs.join(timeout=60)
print(
    f"frames={vs.frame_id} vlm_calls={vs.vlm_calls} "
    f"skipped={vs.skipped_frames} status={vs.last_status!r} err={vs.last_error!r}",
    flush=True,
)
# First frame always calls VLM; static content should skip the rest.
results["visual_gate"] = (
    vs.frame_id >= 8 and 1 <= vs.vlm_calls <= 3 and vs.skipped_frames >= 5
)
results["visual_vlm_calls"] = vs.vlm_calls
results["visual_skipped"] = vs.skipped_frames

print("=" * 60, flush=True)
for k, v in results.items():
    print(f"{k}: {v}")
overall = bool(results.get("fusion")) and bool(results.get("visual_gate")) and bool(results.get("fusion_files"))
print("FUSION_VISUAL_TEST", "PASS" if overall else "FAIL")
sys.exit(0 if overall else 1)
