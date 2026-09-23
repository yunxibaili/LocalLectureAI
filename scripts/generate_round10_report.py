#!/usr/bin/env python3
"""Generate docs/ROUND10_WHISPER_INSTRUMENTATION_REPORT.md from a session.

Observe-only analysis of instrumentation.jsonl + events.jsonl.
Does not change any runtime behavior.

Usage:
  python scripts/generate_round10_report.py sessions/<ts>
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def classify_gap(gap_windows: list[dict], transcription: list[dict]) -> str:
    """A/B/C/D classification for a transcript gap from window events.

    Preference when mixed: majority rules.
      B if >=60% of gap windows were RMS/short-dropped (callbacks existed).
      C if mostly accepted and accepted windows produced zero segments.
      A if there are no window events at all in the gap (or all empty).
      D if accepted windows actually produced segments (other cause).
    """
    if not gap_windows:
        return "A_no_callback_or_empty_windows"

    n = len(gap_windows)
    accepted = [w for w in gap_windows if w.get("accepted")]
    dropped = [w for w in gap_windows if not w.get("accepted")]
    rms_drops = [
        w for w in dropped
        if w.get("drop_reason") == "rms_below_threshold"
        or any(
            s.get("drop_reason") == "rms_below_threshold"
            for s in (w.get("sources") or [])
        )
    ]
    short_drops = [
        w for w in dropped
        if w.get("drop_reason") == "window_too_short"
        or any(
            s.get("drop_reason") == "window_too_short"
            for s in (w.get("sources") or [])
        )
    ]
    drop_like = rms_drops + short_drops
    zero_seg_ids = {t.get("window_id") for t in transcription if t.get("segment_count") == 0}
    accepted_zero = [w for w in accepted if w.get("window_id") in zero_seg_ids]
    accepted_with_segments = [
        w for w in accepted
        if w.get("window_id") not in zero_seg_ids
        and any(
            t.get("window_id") == w.get("window_id") and (t.get("segment_count") or 0) > 0
            for t in transcription
        )
    ]

    # Majority-RMS: callbacks arrived but were filtered before Whisper.
    if len(rms_drops) >= max(1, int(n * 0.6)) and len(accepted_with_segments) == 0:
        return "B_rms_filter"
    if len(short_drops) >= max(1, int(n * 0.6)) and len(accepted_with_segments) == 0:
        return "B_window_too_short"
    if len(drop_like) >= max(1, int(n * 0.6)) and len(accepted_with_segments) == 0:
        return "B_dropped_before_transcription"

    if not accepted and rms_drops and len(rms_drops) >= len(dropped):
        return "B_rms_filter"
    if not accepted and short_drops:
        return "B_window_too_short"
    if not accepted and not rms_drops and not short_drops:
        return "A_no_callback_or_empty_windows"
    if accepted_with_segments:
        return "D_has_segments_but_no_events_or_late"
    if accepted and not transcription:
        return "B_or_C_windows_accepted_but_never_transcribed"
    if accepted_zero and not accepted_with_segments:
        return "C_whisper_or_vad_zero_segments"
    if not accepted and drop_like:
        return "B_dropped_before_transcription"
    return "D_uncertain_instrumentation_insufficient"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: generate_round10_report.py sessions/<ts>")
        return 2
    session = Path(sys.argv[1])
    if not session.is_absolute():
        session = ROOT / session
    if not session.is_dir():
        print(f"session dir not found: {session}")
        return 2

    inst = load_jsonl(session / "instrumentation.jsonl")
    events = load_jsonl(session / "events.jsonl")

    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in inst:
        by_type[r.get("event", "?")].append(r)

    load_events = by_type.get("engine_load_end", []) + by_type.get("audio_bridge_load_end", [])
    load_ms = None
    for e in by_type.get("audio_bridge_load_end", []) + by_type.get("engine_load_end", []):
        if e.get("engine_load_duration_ms") is not None:
            load_ms = e["engine_load_duration_ms"]
            break

    first_cb = by_type.get("first_audio_callback", [])
    windows = by_type.get("audio_window", [])
    accepted_w = [w for w in windows if w.get("accepted")]
    dropped_w = [w for w in windows if not w.get("accepted")]
    rms_drops = [
        w for w in dropped_w
        if w.get("drop_reason") == "rms_below_threshold"
        or any(s.get("drop_reason") == "rms_below_threshold" for s in (w.get("sources") or []))
    ]
    short_drops = [
        w for w in dropped_w
        if w.get("drop_reason") == "window_too_short"
        or any(s.get("drop_reason") == "window_too_short" for s in (w.get("sources") or []))
    ]
    transcriptions = by_type.get("transcription", [])
    zero_seg = by_type.get("zero_segment_window", [])
    transcript_events = by_type.get("transcript_event", [])
    on_no_audio = by_type.get("on_no_audio", []) + by_type.get("on_no_audio_bridge", [])

    # Longest gap between consecutive transcript_event session_t (and vs 0 / end)
    te_ts = sorted(float(e.get("session_t") or 0.0) for e in transcript_events)
    if not te_ts:
        te_ts = sorted(float(e.get("session_t") or 0.0) for e in events)
    longest_gap = None
    if len(te_ts) >= 2:
        best = 0.0
        start = end = None
        for a, b in zip(te_ts, te_ts[1:]):
            g = b - a
            if g > best:
                best = g
                start, end = a, b
        longest_gap = {
            "gap_start_session_t": start,
            "gap_end_session_t": end,
            "duration_s": round(best, 3),
        }
    elif len(te_ts) == 1:
        # gap after first until last window or last event absence
        last_w = max(
            (float(w.get("window_start_s") or 0.0) + float(w.get("duration_s") or 0.0)
             for w in windows),
            default=float(te_ts[0]),
        )
        if last_w > te_ts[0] + 1:
            longest_gap = {
                "gap_start_session_t": te_ts[0],
                "gap_end_session_t": last_w,
                "duration_s": round(last_w - te_ts[0], 3),
            }

    gap_detail = None
    classification = "Instrumentation insufficient"
    if longest_gap and inst:
        gs, ge = longest_gap["gap_start_session_t"], longest_gap["gap_end_session_t"]
        # Map session_t-ish window starts: use window_start_s when available.
        gap_windows = []
        for w in windows:
            ws = float(w.get("window_start_s") or 0.0)
            we = ws + float(w.get("duration_s") or 0.0)
            # windows overlapping [gs, ge] by window_start (approx if session_t ~ window_start)
            if we >= (gs - 5) and ws <= (ge + 5):
                gap_windows.append(w)
        # Prefer transcription events in gap by window_start_s
        gap_tr = []
        for t in transcriptions:
            ws = float(t.get("window_start_s") or 0.0)
            if gs - 30 <= ws <= ge:
                gap_tr.append(t)
        rms_vals = []
        for w in gap_windows:
            for s in w.get("sources") or []:
                if s.get("rms") is not None:
                    rms_vals.append(float(s["rms"]))
            if w.get("rms") is not None and not (w.get("sources") or []):
                rms_vals.append(float(w["rms"]))
        seg_counts = [int(t.get("segment_count") or 0) for t in gap_tr]
        classification = classify_gap(gap_windows, gap_tr)
        if not gap_windows:
            # no window events overlapping gap timestamps — instrumentation may
            # use different clocks; still try classification from all windows
            # between first and last transcript if window_start_s aligns with session_t.
            classification = (
                "D_gap_unaligned_to_window_clock"
                if te_ts else classification
            )
            if classification.startswith("D_") and not te_ts:
                classification = "Instrumentation insufficient"
            # attempt: windows whose window_start falls in gap
            gap_windows = [
                w for w in windows
                if gs <= float(w.get("window_start_s") or 0.0) <= ge
            ]
            if gap_windows:
                classification = classify_gap(gap_windows, [
                    t for t in transcriptions
                    if gs <= float(t.get("window_start_s") or 0.0) <= ge
                ])
                rms_vals = []
                for w in gap_windows:
                    for s in w.get("sources") or []:
                        if s.get("rms") is not None:
                            rms_vals.append(float(s["rms"]))
                    if w.get("rms") is not None and not (w.get("sources") or []):
                        rms_vals.append(float(w["rms"]))
                seg_counts = [
                    int(t.get("segment_count") or 0)
                    for t in transcriptions
                    if gs <= float(t.get("window_start_s") or 0.0) <= ge
                ]

        acc_n = sum(1 for w in gap_windows if w.get("accepted"))
        drop_n = sum(1 for w in gap_windows if not w.get("accepted"))
        gap_detail = {
            **longest_gap,
            "windows": len(gap_windows),
            "accepted": acc_n,
            "dropped": drop_n,
            "rms_drops": sum(
                1 for w in gap_windows
                if w.get("drop_reason") == "rms_below_threshold"
                or any(s.get("drop_reason") == "rms_below_threshold" for s in (w.get("sources") or []))
            ),
            "rms_distribution": {
                "count": len(rms_vals),
                "min": min(rms_vals) if rms_vals else None,
                "max": max(rms_vals) if rms_vals else None,
                "mean": (sum(rms_vals) / len(rms_vals)) if rms_vals else None,
            },
            "segment_counts": seg_counts,
            "total_segments_in_gap": sum(seg_counts),
            "classification": classification,
        }
        if not inst:
            classification = "Instrumentation insufficient"
            gap_detail = None

    # Without any instrumentation events, always insufficient
    if not inst:
        classification = "Instrumentation insufficient"

    drop_counter = Counter()
    for w in dropped_w:
        drop_counter[w.get("drop_reason") or "unknown"] += 1
        for s in w.get("sources") or []:
            if not s.get("accepted"):
                drop_counter[s.get("drop_reason") or "unknown"] += 1

    first_event_t = None
    if events:
        first_event_t = float(events[0].get("session_t") or events[0].get("timestamp") or 0)
    first_inst_t = None
    if transcript_events:
        first_inst_t = float(transcript_events[0].get("session_t") or 0)

    lines: list[str] = []
    lines.append("# Round 10 Whisper Instrumentation Report")
    lines.append("")
    lines.append(f"**Session:** `{session.name}`  ")
    lines.append(f"**Scope:** observe-only; no model/filter/architecture changes.  ")
    lines.append(f"**Instrumentation events:** `{len(inst)}`  ")
    lines.append(f"**Transcript events (events.jsonl):** `{len(events)}`")
    lines.append("")
    lines.append("## 1. Summary stats")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| model load duration | {load_ms if load_ms is not None else 'n/a'} ms |")
    lines.append(
        f"| recorder first callback | "
        f"{'yes @ mono_s=' + str(first_cb[0].get('mono_s')) if first_cb else 'NOT RECORDED'} |"
    )
    lines.append(f"| total windows (audio_window) | {len(windows)} |")
    lines.append(f"| accepted windows | {len(accepted_w)} |")
    lines.append(f"| RMS-dropped windows | {len(rms_drops)} |")
    lines.append(f"| short-window drops | {len(short_drops)} |")
    lines.append(f"| transcribed windows | {len(transcriptions)} |")
    lines.append(f"| zero-segment windows | {len(zero_seg)} |")
    lines.append(f"| total transcript events (instrumentation) | {len(transcript_events)} |")
    lines.append(f"| total transcript events (events.jsonl) | {len(events)} |")
    if longest_gap:
        lines.append(
            f"| longest transcript gap | {longest_gap['duration_s']}s "
            f"(session_t {longest_gap['gap_start_session_t']:.1f} → "
            f"{longest_gap['gap_end_session_t']:.1f}) |"
        )
    else:
        lines.append("| longest transcript gap | n/a (fewer than 2 events) |")
    lines.append(f"| on_no_audio firings | {len(on_no_audio)} |")
    lines.append(f"| drop_reason histogram | {dict(drop_counter) or '{}'} |")
    lines.append("")
    lines.append("## 2. Longest gap breakdown")
    lines.append("")
    if gap_detail:
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        for k, v in gap_detail.items():
            lines.append(f"| {k} | `{v}` |")
        lines.append("")
        lines.append(f"**Classification:** `{gap_detail.get('classification')}`")
        lines.append("")
        lines.append(
            "Three-way meaning: **A** no callback / empty windows · "
            "**B** callback filtered (RMS/short) · "
            "**C** Whisper/VAD produced zero segments · "
            "**D** other / insufficient."
        )
    else:
        lines.append("**Classification:** `Instrumentation insufficient`")
        lines.append("")
        lines.append(
            "No usable gap window detail (missing instrumentation events, "
            "unaligned clocks, or fewer than two transcript events)."
        )
    lines.append("")
    lines.append("## 3. Lifecycle markers")
    lines.append("")
    lines.append("| Event type | Count |")
    lines.append("|---|---|")
    for name in sorted(by_type):
        lines.append(f"| {name} | {len(by_type[name])} |")
    lines.append("")
    lines.append("## 4. Conclusion (do not guess)")
    lines.append("")
    if not inst:
        lines.append(
            "Instrumentation insufficient: `instrumentation.jsonl` missing or empty. "
            "Re-run with `INSTRUMENTATION=true`."
        )
        lines.append("")
        lines.append("**Verdict: Instrumentation insufficient**")
    else:
        lines.append(
            f"Longest gap classified as: **{classification}**."
        )
        lines.append("")
        lines.append(
            "If D / uncertain: Instrumentation insufficient — do not invent a cause."
        )
        lines.append("")
        lines.append(f"**Verdict: {classification}**")
    lines.append("")
    lines.append("## 5. Files")
    lines.append("")
    lines.append(f"- `{session / 'instrumentation.jsonl'}`")
    lines.append(f"- `{session / 'events.jsonl'}`")
    lines.append("")

    out = ROOT / "docs" / "ROUND10_WHISPER_INSTRUMENTATION_REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"verdict: {classification}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
