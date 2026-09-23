#!/usr/bin/env python3
"""Round 10: capture ~5 minutes of system/mic audio with instrumentation ON.

Audio-only (no vision / no final summary). Writes:
  sessions/<ts>/
    instrumentation.jsonl   (INSTRUMENTATION sink)
    events.jsonl           (TranscriptEvent)
    capture_meta.json

Usage (from repo root):
  set INSTRUMENTATION=true
  .venv\\Scripts\\python.exe scripts\\round10_capture_audio.py --seconds 300

Play course audio during the run (teacher speech + pauses + slides).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "third_party" / "Hearsay" / "src"))

os.environ.setdefault("INSTRUMENTATION", "true")
os.environ.setdefault("WHISPER_LANGUAGE", "zh")
os.environ.setdefault("WHISPER_DEVICE", "auto")

from app.course_session.audio_bridge import AudioBridge  # noqa: E402
from app.course_session.settings import (  # noqa: E402
    AUDIO_SOURCE,
    INSTRUMENTATION,
    WHISPER_COMPUTE,
    WHISPER_DEVICE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("round10_capture")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300.0)
    args = ap.parse_args()

    if not INSTRUMENTATION:
        print("INSTRUMENTATION is off — set INSTRUMENTATION=true")
        return 2

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = ROOT / "sessions" / ts
    session_dir.mkdir(parents=True, exist_ok=True)

    from hearsay.utils import instrumentation as inst

    inst.disable_for_tests()
    inst.set_output(session_dir / "instrumentation.jsonl")

    events_path = session_dir / "events.jsonl"
    transcript_md = session_dir / "transcript.md"
    status_log: list[str] = []

    def on_status(msg: str) -> None:
        status_log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        print(f"STATUS {time.strftime('%H:%M:%S')} {msg}", flush=True)

    events_written = 0

    def on_event(ev) -> None:
        nonlocal events_written
        rec = {
            "timestamp": time.time(),
            "session_t": getattr(ev, "session_t", None),
            "source": getattr(ev, "source", None),
            "window_start": getattr(ev, "window_start", None),
            "text": getattr(ev, "text", ""),
        }
        try:
            with events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            events_written += 1
        except Exception as e:
            log.error("write event failed: %s", e)

    def on_fatal(exc: Exception) -> None:
        on_status(f"FATAL {exc!r}")

    def on_no_audio() -> None:
        on_status("on_no_audio (60s silence alert)")

    t0 = time.monotonic()
    bridge = AudioBridge(
        on_event=on_event,
        transcript_dir=session_dir,
        t0=t0,
        on_fatal=on_fatal,
        on_no_audio=on_no_audio,
    )
    # Keep status callbacks on bridge if supported
    if hasattr(bridge, "set_status_callback"):
        bridge.set_status_callback(on_status)

    meta = {
        "session_dir": str(session_dir),
        "seconds": args.seconds,
        "INSTRUMENTATION": INSTRUMENTATION,
        "AUDIO_SOURCE": AUDIO_SOURCE,
        "WHISPER_MODEL": WHISPER_MODEL,
        "WHISPER_LANGUAGE": WHISPER_LANGUAGE,
        "WHISPER_DEVICE": WHISPER_DEVICE,
        "WHISPER_COMPUTE": WHISPER_COMPUTE,
        "started_iso": datetime.now().isoformat(timespec="seconds"),
    }
    (session_dir / "capture_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"capture start dir={session_dir} seconds={args.seconds}", flush=True)
    bridge.start()
    try:
        end = t0 + args.seconds
        while time.monotonic() < end:
            time.sleep(5)
            left = end - time.monotonic()
            print(
                f"SNAP left={left:.0f}s events={events_written} "
                f"status={getattr(bridge, 'last_status', '?')} "
                f"chunks={getattr(bridge, 'chunks_transcribed', '?')} "
                f"emitted={getattr(bridge, 'events_emitted', '?')}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("interrupted", flush=True)
    finally:
        print("stopping bridge...", flush=True)
        try:
            bridge.stop()
        except Exception as e:
            print(f"stop error: {e}", flush=True)
        inst.emit(
            "round10_capture_end",
            events_written=events_written,
            wall_s=round(time.monotonic() - t0, 3),
        )

    meta["ended_iso"] = datetime.now().isoformat(timespec="seconds")
    meta["events_written"] = events_written
    (session_dir / "capture_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if status_log:
        (session_dir / "status.log").write_text(
            "\n".join(status_log) + "\n", encoding="utf-8"
        )

    print(f"done events={events_written} dir={session_dir}", flush=True)
    # Generate report from this session
    import subprocess

    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_round10_report.py"), str(session_dir)],
        cwd=str(ROOT),
    )
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
