"""Round 13 real-session replay: drive NoteEngine.fuse via chat_fusion on real events.

Reads a real session's events.jsonl + visual_events.jsonl, feeds the last
RETRAN window into CourseSession._fuse_once (new path: format=json + thinking
harvest), and reports whether at least one success updated live_notes/course_state.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.course_session.session import CourseSession  # noqa: E402
from app.course_session.storage import SessionStorage  # noqa: E402
from app.course_session.note_engine import NoteEngine  # noqa: E402
from app.course_session.events import TranscriptEvent, VisualEvent  # noqa: E402
from core.capture import Region  # noqa: E402


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    src = ROOT / "sessions" / "2026-09-23_13-04-21"
    if not src.exists():
        print(f"FAIL source missing: {src}")
        return 1

    events = _load_jsonl(src / "events.jsonl")
    visuals = _load_jsonl(src / "visual_events.jsonl")
    # Prefer the last ~80 non-empty transcripts (covers window + backlog).
    tr_dicts = [e for e in events if (e.get("text") or "").strip()][-80:]
    vi_dicts = visuals[-10:]
    print(f"SOURCE {src.name}: {len(tr_dicts)} transcript / {len(vi_dicts)} visual loaded (from {len(events)}/{len(visuals)})")

    # Fresh session dir so we never mutate the source session.
    sess = CourseSession(Region(10, 10, 50, 50))
    sess.storage.ensure_live_header("Round13Replay")
    sess.note_engine = NoteEngine(sess.storage)

    for d in tr_dicts:
        with sess._lock:
            sess._unfused_transcripts.append(TranscriptEvent(
                timestamp=float(d.get("timestamp") or 0.0),
                duration=float(d.get("duration") or 0.0),
                text=str(d.get("text") or ""),
                source=str(d.get("source") or "system_audio"),
                session_t=float(d.get("session_t") or 0.0),
            ))
    for d in vi_dicts:
        with sess._lock:
            sess._unfused_visuals.append(VisualEvent(
                timestamp=float(d.get("timestamp") or 0.0),
                frame_id=int(d.get("frame_id") or 0),
                description=str(d.get("description") or ""),
                changed=bool(d.get("changed", True)),
                source=str(d.get("source") or "screen"),
                session_t=float(d.get("session_t") or 0.0),
                diff_score=float(d.get("diff_score") or 0.0),
            ))

    print(f"pending before: tr={len(sess._unfused_transcripts)} vi={len(sess._unfused_visuals)}")
    sess.running = True

    successes = 0
    max_attempts = 6
    for i in range(1, max_attempts + 1):
        before = len(sess._unfused_transcripts)
        try:
            sess._fuse_once()
        except Exception as e:
            print(f"attempt {i}: EXC {type(e).__name__}: {e}")
            traceback.print_exc()
        status_path = sess.storage.realtime_fusion_status_path
        lines = []
        if status_path.exists():
            for ln in status_path.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    lines.append(json.loads(ln))
        last = lines[-1] if lines else {}
        after = len(sess._unfused_transcripts)
        print(
            f"attempt {i}: result={last.get('result')} pending={last.get('pending_transcript_count')} "
            f"fused={last.get('fused_transcript_count')} stage={last.get('parse_stage')} "
            f"http={last.get('http_status')} lat={last.get('latency_ms')}ms "
            f"resp_chars={last.get('response_chars')} left={after}"
        )
        if last.get("result") == "success":
            successes += 1
            if after < before:
                break

    live = sess.storage.live_notes_path
    cstate = sess.storage.course_state_path
    status = sess.storage.realtime_fusion_status_path
    live_sz = live.stat().st_size if live.exists() else 0
    cs_lines = 0
    if cstate.exists():
        cs_lines = sum(1 for ln in cstate.read_text(encoding="utf-8").splitlines() if ln.strip())
    st_lines = []
    if status.exists():
        st_lines = [json.loads(ln) for ln in status.read_text(encoding="utf-8").splitlines() if ln.strip()]

    results = [ln.get("result") for ln in st_lines]
    updates = sess.note_engine.state.updates
    print("---")
    print(f"session_dir={sess.storage.dir.name}")
    print(f"status_results={results}")
    print(f"successes={successes} updates={updates} live_notes_bytes={live_sz} course_state_lines={cs_lines}")
    print(f"final_pending tr={len(sess._unfused_transcripts)} vi={len(sess._unfused_visuals)}")

    ok = successes >= 1 and updates >= 1 and live_sz > 0 and cs_lines >= 1
    if ok:
        print("REPLAY PASS: at least one realtime fusion success updated live_notes + course_state")
        return 0
    print("REPLAY FAIL: no success / notes not updated")
    print("Root Cause: see status_results / parse_stage / http above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
