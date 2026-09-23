"""Round 10 instrumentation gate tests: off by default, on writes JSONL.

No hardware / no Whisper required. Run:
  python tests/test_round10_instrumentation.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAILURES = []


def check(cond, msg):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {msg}")
    if not cond:
        FAILURES.append(msg)


def main() -> int:
    # Ensure clean env
    os.environ.pop("INSTRUMENTATION", None)
    os.environ.pop("INSTRUMENTATION_PATH", None)

    from hearsay.utils import instrumentation as inst

    inst.disable_for_tests()
    check(inst.enabled() is False, "disabled when INSTRUMENTATION unset")

    tmp = Path(tempfile.mkdtemp(prefix="inst_off_")) / "i.jsonl"
    inst.set_output(tmp)
    inst.emit("should_not_write", foo=1)
    check(not tmp.exists(), "no file when disabled")

    # Enable
    os.environ["INSTRUMENTATION"] = "true"
    inst.disable_for_tests()
    check(inst.enabled() is True, "enabled when INSTRUMENTATION=true")

    out = Path(tempfile.mkdtemp(prefix="inst_on_")) / "instrumentation.jsonl"
    inst.set_output(out)
    inst.emit(
        "audio_window",
        window_id=1,
        rms=0.00001,
        accepted=False,
        drop_reason="rms_below_threshold",
        duration_s=30,
        sources=[{"source": "system", "rms": 0.00001, "accepted": False,
                  "drop_reason": "rms_below_threshold", "window_duration_s": 30}],
    )
    inst.emit("engine_load_end", engine_load_duration_ms=2400, model="turbo")
    inst.emit("first_audio_callback", source="system")
    check(out.exists(), "JSONL written when enabled")
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    check(len(rows) == 3, f"3 events (got {len(rows)})")
    check(rows[0]["event"] == "audio_window" and rows[0]["drop_reason"] == "rms_below_threshold",
          "audio_window fields present")
    check(rows[1]["engine_load_duration_ms"] == 2400, "engine load duration present")
    check(rows[2]["event"] == "first_audio_callback", "first callback event present")
    check(all("timestamp" in r and "seq" in r for r in rows), "timestamp+seq on every row")

    # Threshold unchanged
    from hearsay.constants import SILENCE_RMS_FLOOR, CHUNK_DURATION_S
    check(SILENCE_RMS_FLOOR == 1e-4, f"RMS floor unchanged: {SILENCE_RMS_FLOOR}")
    check(CHUNK_DURATION_S == 30, f"CHUNK_DURATION_S unchanged: {CHUNK_DURATION_S}")

    # Default OFF restores
    os.environ.pop("INSTRUMENTATION", None)
    inst.disable_for_tests()
    check(inst.enabled() is False, "default remains off after enable test")

    # emit must never raise when path unwritable-ish
    os.environ["INSTRUMENTATION"] = "true"
    inst.disable_for_tests()
    try:
        inst.set_output("Z:\\__no_such_root__\\x\\instrumentation.jsonl")
        inst.emit("boom")
        check(True, "set_output+emit never raise on bad path")
    except Exception as e:
        check(False, f"emit raised: {e}")
    finally:
        os.environ.pop("INSTRUMENTATION", None)
        inst.disable_for_tests()

    # Report generator: synthetic RMS-drop gap classifies as B
    try:
        import subprocess

        repo = Path(__file__).resolve().parents[3]
        fix = Path(tempfile.mkdtemp(prefix="r10_gap_"))
        # fake session with one gap of rms-dropped windows
        rows = []
        rows.append({"timestamp": 1.0, "event": "engine_load_end", "engine_load_duration_ms": 2400})
        rows.append({"timestamp": 2.0, "event": "first_audio_callback", "source": "system"})
        rows.append({
            "timestamp": 3.0, "event": "audio_window", "window_id": 0,
            "window_start_s": 0, "duration_s": 30, "rms": 0.01, "accepted": True,
        })
        rows.append({
            "timestamp": 40.0, "event": "transcript_event", "session_t": 45.0,
            "window_id": 0, "text_length": 10,
        })
        for i in range(1, 7):  # gap ~180s of silent windows
            rows.append({
                "timestamp": 40.0 + i * 30, "event": "audio_window", "window_id": i,
                "window_start_s": i * 30, "duration_s": 30, "rms": 1e-6,
                "accepted": False, "drop_reason": "rms_below_threshold",
                "sources": [{
                    "source": "system", "rms": 1e-6, "accepted": False,
                    "drop_reason": "rms_below_threshold", "window_duration_s": 30,
                }],
            })
            rows.append({
                "timestamp": 40.0 + i * 30, "event": "transcription", "window_id": i,
                "segment_count": 0, "transcribe_duration_ms": 0,
            })
        rows.append({
            "timestamp": 250.0, "event": "audio_window", "window_id": 7,
            "window_start_s": 210, "duration_s": 30, "rms": 0.02, "accepted": True,
        })
        rows.append({
            "timestamp": 251.0, "event": "transcription", "window_id": 7,
            "segment_count": 3, "transcribe_duration_ms": 400,
        })
        rows.append({
            "timestamp": 252.0, "event": "transcript_event", "session_t": 240.0,
            "window_id": 7, "text_length": 20,
        })
        (fix / "instrumentation.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
        )
        ev_rows = [
            {"timestamp": 100.0, "session_t": 45.0, "text": "a"},
            {"timestamp": 300.0, "session_t": 240.0, "text": "b"},
        ]
        (fix / "events.jsonl").write_text(
            "\n".join(json.dumps(r) for r in ev_rows), encoding="utf-8"
        )
        py = str(repo / ".venv" / "Scripts" / "python.exe")
        if not Path(py).exists():
            py = sys.executable
        r = subprocess.run(
            [py, str(repo / "scripts" / "generate_round10_report.py"), str(fix)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            cwd=str(repo),
        )
        err_snip = (r.stderr or "")[-120:].encode("ascii", "replace").decode("ascii")
        out_snip = (r.stdout or "").strip().encode("ascii", "replace").decode("ascii")
        check(r.returncode == 0, f"report generator exit 0 (got {r.returncode}: {err_snip})")
        report = (repo / "docs" / "ROUND10_WHISPER_INSTRUMENTATION_REPORT.md")
        body = report.read_text(encoding="utf-8") if report.exists() else ""
        check(
            "B_rms_filter" in body or "rms_below_threshold" in body,
            f"synthetic report has rms classification ({out_snip})",
        )
    except Exception as e:
        msg = str(e).encode("ascii", "replace").decode("ascii")
        check(False, f"report generator smoke failed: {msg}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("ROUND10_INSTRUMENTATION PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
