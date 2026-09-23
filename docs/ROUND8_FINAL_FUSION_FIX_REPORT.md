# Round 8 Final Fusion Fix Report

**Base:** `7f7016d`  
**Scope:** Fix the real Round 7 Final Fusion failure only.

## 1. Round 7 Real Failure

Round 7 real session:

`sessions/2026-09-23_10-53-54`

Observed result:

* `transcript.md`: 11420 bytes
* `visual_events.jsonl`: 5583 bytes, 8 visual events
* `course_state.jsonl`: 14389 bytes, 5 updates
* `live_notes.md`: 10619 bytes
* `partial_notes.md`: 40898 bytes
* `final_summary.md`: missing before this fix

`partial_notes.md` reason:

```text
Final Fusion failure — full final_summary.md skipped
```

## 2. Root Cause

The failure was not context overflow.

Using the real Round 7 tail data and final CourseState:

* Final Fusion prompt: 3217 chars / 4886 bytes before real CourseState, and
  3217 chars with the final CourseState reconstruction used for the timeout
  probe.
* `num_ctx=16384`
* The model request did not fail with HTTP 500 or invalid JSON.
* The application did not pass an explicit timeout to Final Fusion.
* `chat_text()` therefore used QLens' default `REQUEST_TIMEOUT=120`.
* The real prompt exceeded 120 seconds and raised:

```text
ReadTimeout: HTTPConnectionPool(host='localhost', port=11434): Read timed out. (read timeout=120)
elapsed=122.09s
```

This exception was caught inside `_chat_fusion()` and converted into the
generic `Final Fusion failure`.

## 3. Minimal Fix

Changed only Final Fusion behavior:

* `app/course_session/note_engine.py`
  * Final Fusion now calls `chat_text(..., think=False)`.
  * Final Fusion now passes an explicit timeout:
    `max(REQUEST_TIMEOUT, 300)`.
  * Realtime fusion behavior is unchanged.
  * Error logging now includes exception type.

Why this is minimal:

* The prompt size is small and within context.
* The expensive part is 27B generation with thinking enabled.
* Summary already uses `think=False`; Final Fusion now follows the same pattern.
* No chunking, schema change, model change, or realtime architecture change.

## 4. Regression Test

Added Test AH in `test_stability.py`:

* Final Fusion uses `think=False`.
* Final Fusion passes an explicit timeout `>=300`.
* Existing Final Fusion success/failure/timeout/no-op tests still pass.

## 5. Real Round 7 Data Regression

Re-ran Final Fusion and Final Summary offline using the real Round 7 session
artifacts:

* pending transcripts: 40
* pending visuals: 6
* CourseState updates: 5
* Final Fusion result: `success`
* Final Summary result: `sessions/2026-09-23_10-53-54/final_summary.md`
* `final_summary.md` size: 9592 bytes
* Final Fusion + Summary elapsed: 675.39s
* Final `/api/ps`: `{"models":[]}`

The generated summary contains the tail topic and key content:

* 学习方法
* 听懂不等于学会
* 基础与综合题
* 每天至少三小时
* 第一章系统梳理基础

## 6. Verification

* `test_stability.py`: PASS
* AST: PASS
* Hearsay `test_pipeline_writer.py`: ALL CHECKS PASSED
* Real Round 7 session regression: PASS
* Final Summary generated: yes
* Final `/api/ps`: `{"models":[]}`

## 7. Status

The Round 7 Final Fusion failure is fixed for the real session data.

No changes were made to Whisper, vision quality, prompts unrelated to Final
Fusion, model roles, or realtime lifecycle.
