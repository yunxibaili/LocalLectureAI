# Round 10 Whisper Instrumentation Report

**Base:** `20ad23a`  
**Scope:** Observe-only instrumentation to classify the Round 7 ~190s transcript gap (A/B/C/D). No model, threshold, VAD, architecture, or summary behavior changes.  
**Runtime gate:** `INSTRUMENTATION=true|1|yes|on` (default **off**). When off, every `emit()` is a no-op.

## 1. What was added

| Layer | File | Events |
|---|---|---|
| Helper | `third_party/Hearsay/src/hearsay/utils/instrumentation.py` | `emit` / `set_output` / `enabled` (JSONL, never raises) |
| Capture | `third_party/Hearsay/src/hearsay/audio/recorder.py` | `first_audio_callback`, `recorder_started/stopped`, `audio_window` (rms / accepted / drop_reason), `window_queued`, `window_queue_dropped`, `audio_window_tick`, `on_no_audio` |
| Engine | `third_party/Hearsay/src/hearsay/transcription/engine.py` | `engine_load_start/end`, `engine_transcribe` (duration_ms, segment_count, vad fields) |
| Pipeline | `third_party/Hearsay/src/hearsay/transcription/pipeline.py` | `pipeline_window_start`, `transcription`, `zero_segment_window`, `result_enqueued`, `pipeline_error` |
| Bridge | `app/course_session/audio_bridge.py` | load/start markers, `transcript_event`, `result_empty_segments`, fatal / no_audio |
| Session | `app/course_session/session.py` | points sink to `sessions/<ts>/instrumentation.jsonl` when on |
| Settings / launcher | `app/course_session/settings.py`, `start_course.ps1` | `INSTRUMENTATION` flag (default false) |
| Report tools | `scripts/generate_round10_report.py`, `scripts/round10_capture_audio.py` | analysis + ~5 min audio-only capture |

**Not changed:** `SILENCE_RMS_FLOOR` (`1e-4`), `CHUNK_DURATION_S` (30), VAD params, Whisper model, CourseSession fusion, QLens, final summary.

**Hearsay patch note:** `docs/hearsay.patch` no longer claims zero source modification — Round 10 adds gated instrumentation files listed above (see that file’s header).

## 2. Verification

| Check | Result |
|---|---|
| `third_party/Hearsay/tests/test_round10_instrumentation.py` | **PASS** (gate off/on, JSONL fields, thresholds unchanged, report generator → `B_rms_filter` on synthetic RMS gap) |
| `third_party/Hearsay/tests/test_pipeline_writer.py` | **ALL CHECKS PASSED** |
| `test_stability.py` | **STABILITY_UNIT_TEST PASS** (exit 0) |
| `py_compile` on all touched modules | **OK** |
| Default path with `INSTRUMENTATION` unset | no file, no behavior change |

## 3. Round 7 gap (what this round must answer)

From `docs/ROUND9_WHISPER_STARTUP_REPORT.md` / `sessions/2026-09-23_10-53-54`:

- First transcript ~**45s** after start (so not “Whisper never started”).
- Then a **~190s** hole with no `events.jsonl` rows (10:54:39 → 10:57:49).
- Stable production after that.

Round 7 ran **before** this instrumentation existed — that session has no `instrumentation.jsonl`. The 190s hole cannot be replayed from old artifacts alone.

## 4. Real ~5 minute capture (this round)

| Item | Value |
|---|---|
| Command | `INSTRUMENTATION=true .venv\Scripts\python.exe scripts\round10_capture_audio.py --seconds 300` |
| Session | `sessions/2026-09-23_12-17-13` |
| Wall window | 12:17:13 → 12:22:18 (~305s recording after load) |
| Whisper load | **44050 ms** (`engine_load_duration_ms=43945`, bridge total 44050) |
| First audio callback | **yes**, immediately after `recorder_started` (~80 ms) |
| Source | `system` (WASAPI loopback), model `turbo` / cuda / float16, language `zh` |
| Total windows | **9** (8 full 30s + final partial 20.6s) |
| Accepted windows | **9 / 9** |
| RMS-dropped | **0** |
| Short-window drops | **0** |
| Transcribed windows | **9 / 9** |
| Zero-segment windows | **0** |
| Segment counts per window | 11, 16, 25, 20, 11, 1, 12, 15, 1 |
| Transcribe time / window | 174–1115 ms (fast) |
| Transcript events (`events.jsonl`) | **112** |
| Transcript events (instrumentation) | **112** |
| `on_no_audio` firings | **0** |
| `drop_reason` histogram | `{}` (empty) |
| First transcript `session_t` | **75.4s** (bridge t0 includes ~44s load; first 30s window completes ~30s after record start) |
| Longest transcript-event gap | **30.35s** (`session_t` 224.941 → 255.293) |

### Longest gap breakdown (this capture)

| Field | Value |
|---|---|
| duration_s | 30.352 |
| windows overlapping gap | 2 (starts ~210.2 and ~240.3) |
| accepted | 2 |
| dropped | 0 |
| rms_drops | 0 |
| RMS | 0.019–0.029 (well above `1e-4`) |
| segment_counts | [15, 1] |
| classification | `D_has_segments_but_no_events_or_late` |

Interpretation of the **30s** gap: this is ordinary **chunk-cadence** (30s cut + segment timestamps inside the window), **not** an A/B/C failure. Both windows were accepted and produced segments.

## 5. Classification answers

### 5.1 This 5-minute real segment

| Question | Answer |
|---|---|
| A — no audio callback / no windows? | **No.** `first_audio_callback` present; 9/9 windows emitted. |
| B — callbacks filtered by RMS/short? | **No.** 0 drops; RMS ~0.02–0.04 ≫ `1e-4`. |
| C — entered Whisper but 0 segments? | **No.** 0 zero-segment windows; every window produced text. |
| D — other? | Longest gap **30.4s** = normal 30s window boundary with segments present (`D_has_segments_but_no_events_or_late` in the automated sense = events exist on both sides; gap length ≈ chunk length). |

### 5.2 Round 7’s original ~190s gap

**Verdict: Instrumentation insufficient.**

The ~190s hole was **not re-observed** in this ~5 minute real segment. Without a re-occurrence under instrumentation (or instrumentation retrofitted onto that exact session — impossible after the fact), the gap cannot be honestly labeled A, B, C, or D.

What this round **does** establish for Round 7:

1. Instrumentation can distinguish A/B/C/D on live data.
2. On healthy course/system audio with speech, the pipeline is **9/9 accepted, 0 RMS drops, 0 zero-segment** — so a future ~190s hole with the same markers would be **directly classifiable**.
3. Round 9 remains correct: load ~44s + first transcript ~75s from bridge t0 does **not** explain a mid-session 190s hole.
4. The only 30s-scale silence seen here is expected chunk timing, not a filter bug.

## 6. Twelve required observations (live capture)

| # | Required | Observed |
|---:|---|---|
| 1 | Engine load duration | 44050 ms |
| 2 | First audio callback time | yes, ~80 ms after recorder start |
| 3 | Total windows | 9 |
| 4 | Accepted windows | 9 |
| 5 | RMS-dropped windows | 0 |
| 6 | Short drops | 0 |
| 7 | Transcribed windows | 9 |
| 8 | Zero-segment windows | 0 |
| 9 | `on_no_audio` | 0 firings |
| 10 | Total transcript events | 112 |
| 11 | Longest gap (start/end/duration) | 224.941 → 255.293, **30.352s** |
| 12 | Gap windows / accepted / dropped / RMS / segments | 2 / 2 / 0 / ~0.019–0.029 / 15+1 |

## 7. How to re-run

```powershell
$env:INSTRUMENTATION = "true"
# optional: .venv\Scripts\python.exe scripts\round10_capture_audio.py --seconds 300
.\start_course.ps1   # GUI course session; sink → sessions/<ts>/instrumentation.jsonl
.\stop_course.ps1
python scripts\generate_round10_report.py sessions\<ts>
```

Leave `INSTRUMENTATION` unset/`false` for normal use (no JSONL, no extra I/O).

## 8. Conclusion (stop here)

- **Implementation:** done, gated, tests green, thresholds unchanged.
- **Live 5-min data:** healthy path fully instrumented; **no A/B/C failure**.
- **Round 7 ~190s gap:** **Instrumentation insufficient** — not reproduced under instrumentation; **do not guess a cause**.
- **Next:** independent review of this report + `sessions/2026-09-23_12-17-13/instrumentation.jsonl`. Stop Round 10 here.

**Verdict: Instrumentation insufficient for the Round 7 190s gap (not reproduced); A/B/C ruled out on the 5-minute healthy capture; largest observed gap 30.4s = normal chunk cadence.**
