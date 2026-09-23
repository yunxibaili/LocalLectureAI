# Round 9 Whisper Startup Report

**Base:** `20ad23a`  
**Scope:** Investigate why real course transcript appeared delayed at startup. No code changes.

## 1. Session Examined

Round 7 real course session:

`sessions/2026-09-23_10-53-54`

Artifacts used:

* `events.jsonl`
* `transcript.md`
* `course_state.jsonl`
* `live_notes.md`
* current Hearsay recorder / pipeline / engine code

No Hearsay log exists for 2026-09-23 under `%APPDATA%\Hearsay\logs`.
Only the 2026-09-22 log exists, so raw runtime timing from Hearsay logging is
not available for this session.

## 2. Actual Timeline

From `events.jsonl`:

| Time | Session offset | Event |
|---|---:|---|
| 10:53:54 | 0s | LocalLectureAI course session started |
| 10:54:39 | ~45s | First transcript events appeared |
| 10:54:39 | ~49s | First segment text: “你要不要做一下” |
| 10:54:39 | ~61s | Second segment text: “看看能记得多少” |
| 10:57:49 | ~252s | Next transcript-producing chunk appeared |
| 10:58:07 | ~259s | Next transcript event |
| 11:00:10 | ~394s | Transcript production resumed with longer content |
| 11:09:18 | ~934s | Last transcript event |

`transcript.md` creation time is `10:54:39`, matching the first transcript
event.

## 3. Finding

The evidence does **not** support “Whisper took 5 minutes to load”.

The first transcript appeared about 45 seconds after session start. The long
delay before *stable* transcript production was a roughly 190-second gap between
10:54:39 and 10:57:49 with no transcript events.

The exact cause of that gap cannot be determined from the current evidence.

Possible causes that remain compatible with the evidence:

* The course intro audio was actually silent/quiet until the teacher began.
* Hearsay dropped windows because RMS was below `SILENCE_RMS_FLOOR=1e-4`.
* Whisper VAD filtered low-volume/music/background audio.
* System-audio capture delivered no usable samples during that interval.
* GPU contention slowed processing, but this does not explain a 190-second gap
  because the first transcript already appeared at ~45s.

What can be ruled out as the primary cause:

* Whisper model load alone: first transcript was produced at ~45s.
* 30-second chunk duration alone: that explains only up to ~30s initial delay.
* 27B interference: only `qwen3-vl:8b` was resident during realtime.

## 4. Current Hearsay Parameters

Observed code settings:

* Whisper model: `turbo`
* Device: CUDA detected, realtime startup log reported `whisper cuda`
* Language: `zh`
* VAD filter: `True`
* VAD `min_silence_duration_ms`: `500`
* Chunk duration: `30s`
* Overlap: `1s`
* Minimum window size: `1s`
* Silence RMS floor: `1e-4`
* Silence alert: `60s`, re-alert every `120s`

Hearsay `_emit_window()` silently skips windows when:

* audio length `< 1 second`
* RMS `< 1e-4`

It does not currently log dropped windows or their RMS values.

## 5. Why Root Cause Is Not Yet Provable

The missing evidence is:

* recorder callback first-sample timestamp
* per-window RMS
* whether each 30s window was emitted or dropped
* Whisper VAD result for each chunk
* chunk transcription duration
* whether `on_no_audio` fired during the 190s gap

Without those, we can prove that the gap is a **transcript-producing window
gap**, but cannot prove whether the cause was true course silence, VAD, RMS
filtering, or capture dropout.

## 6. Recommended Minimal Investigation

Do not change models or architecture.

Add temporary instrumentation in the next investigation round:

1. Log `AudioBridge.start()` begin/end and Whisper load duration.
2. Log recorder first callback timestamp.
3. Log each 30s window:
   * chunk index
   * duration
   * RMS
   * emitted/dropped
4. Log pipeline chunk start/end and Whisper transcribe duration.
5. Log segment count and language probability.
6. Log `on_no_audio` callbacks.

Then run a short 5-minute real course segment and compare whether the gap is
explained by true silence or by dropped/filtered capture.

## 7. Recommended Fix Direction

No fix is justified yet.

If instrumentation shows dropped low-RMS windows during real speech:

* lower or bypass the RMS gate for system audio
* or transcribe shorter fallback windows

If instrumentation shows VAD filtering real speech:

* relax VAD parameters for this use case

If instrumentation shows true silence in the course intro:

* treat this as expected behavior
* add UI status indicating “no speech captured yet”

If instrumentation shows capture dropout:

* investigate WASAPI loopback / device lifecycle

## 8. Conclusion

Root cause status: **not uniquely determined from current evidence**.

Confirmed:

* First transcript at ~45s, not 5 minutes.
* Stable transcript delay corresponds to a ~190s gap with no transcript
  events.
* Whisper model loading is not the primary cause.
* Current code lacks the instrumentation needed to distinguish true silence,
  RMS filtering, VAD filtering, and capture dropout.
