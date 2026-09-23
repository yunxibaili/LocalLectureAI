# Round 12: Realtime Fusion Structured Diagnostics + Pending Preservation

**Date:** 2026-09-23  
**Goal:** Tonight's New Oriental calculus live class — never lose raw information; realtime Fusion is an enhancement only. Fusion failure must not fail audio/visual/transcript/session. Post-class Final Fusion / Final Summary must still consume retained pending events.

**Honesty:** This round adds **diagnostics only**. No real model evidence that realtime Fusion itself is fixed. No claim that realtime Fusion succeeds in production.

---

## What Changed

### 1. `app/course_session/note_engine.py`

- Added realtime fuse outcome constants: `FUSE_OK`, `FUSE_SKIP`, `FUSE_INVALID_JSON`, `FUSE_TIMEOUT`, `FUSE_EXCEPTION`, `FUSE_EMPTY`, `FUSE_FAILURE`.
- `NoteEngine` now keeps `last_fuse` — structured diag for the **last** `fuse()` call:
  - `result`, `input_chars`, `exception_type`, `error_message`, `latency_ms`
- `fuse()` still returns **bool only** (`True` = applied delta). Behavior contract unchanged:
  - success → apply + `mark_model_loaded`
  - skip / invalid JSON / empty / chat failure → `False`, no registry mark, pending not consumed by caller
- `fuse()` wraps work in try/finally: always refreshes `last_fuse`; ordinary chat failures no longer escape as uncaught exceptions from `fuse()` (classified as `exception`/`timeout`/`failure` instead).
- `_chat_fusion` records chat-level exception type/message and timeout heuristic (`timeout` / `timed out` / `readtimeout` in name+message) without changing its `Optional[str]` return.
- **No full prompt / no full model response** stored in `last_fuse`.

### 2. `app/course_session/session.py`

- `CourseSession.__init__`: `_fuse_attempt_id = 0`.
- `_fuse_once()` (Round 6 snapshot→fuse→identity-consume preserved):
  - Wraps `note_engine.fuse()` in try/except — unexpected exceptions never drop pending, never set `FAILED`.
  - Writes **one JSONL line per attempt** to `storage.realtime_fusion_status_path`.
  - Status fields: `timestamp`, `attempt_id`, `pending_transcript_count`, `pending_visual_count`, `input_chars`, `result`, `latency_ms`, `exception_type`, `error_message`.
  - `result` precedence: session-level exception override → `last_fuse.result` → `success`/`failure`.
  - Empty pending early-return writes **no** status line (absence = “no pending”, not “silent fail”).
- `_write_fusion_status()` best-effort: status write failure never fails fusion.
- Status line is **always on** (not gated by `INSTRUMENTATION`).
- Fusion path never sets `SessionState.FAILED`, never stops capture workers.

### 3. `app/course_session/storage.py`

- New path: `realtime_fusion_status_path = dir / "realtime_fusion_status.jsonl"`.
- New helper: `append_realtime_fusion_status(obj)`.

### 4. `test_stability.py` — Round 12 tests AI–AT

| Test | Scenario | Assertions |
|------|----------|------------|
| **AI** | invalid JSON | pending kept; `result=invalid_json`; required keys; pending counts; no registry mark |
| **AJ** | chat exception | pending kept; `result=exception`; `exception_type`; error_message; state ≠ FAILED |
| **AK** | timeout | pending kept; `result=timeout`; timeout message |
| **AL** | fuse returns False (empty chat) | pending kept; `result=failure`; no registry mark |
| **AM** | skip=True | pending kept; `result=skip`; no registry mark |
| **AN** | mid-fuse new event + success | snapshot consumed; mid-fuse kept; `result=success`; pre-fuse counts; `latency_ms` |
| **AO** | two consecutive fusion failures | state stays RUNNING; running True; pending intact; attempt_id 1,2; failure-class results |
| **AP** | privacy | no transcript text; no raw model body; status file stays small; pending kept while writing |
| **AQ** | fuse() raises RuntimeError | no raise out of `_fuse_once`; pending kept; `result=exception` + type; recovery drain works |
| **AR** | NoteEngine.last_fuse unit | empty / invalid_json / skip / success / exception classifications + `input_chars` |
| **AT** | realtime failure → Final Phase | tail pending; status written; tail fed to Final Fusion; final_summary written; StopResult success |

Existing Round 6/8 tests (X–Z, AA–AH) unchanged and still pass.

---

## What Did NOT Change

- Models: `qwen3-vl:8b` / `qwen38-27b-main` (settings unchanged)
- FINAL fusion timeout (still explicit long timeout / `think=False`)
- Whisper / RMS / VAD / chunk duration / instrumentation gate behavior
- QLens / Hearsay core
- Realtime fusion **behavior** (only diagnostics + safety wrap; no skip/JSON semantics change)
- Final Phase success/failure honesty (Round 6/8)
- `/api/ps` empty models list behavior (verified: `{"models":[]}`)

---

## Test Results

- `py_compile` on `note_engine.py`, `session.py`, `storage.py`, `test_stability.py`: **OK**
- `python test_stability.py`: **STABILITY_UNIT_TEST PASS** (all Round 12 AI–AT green + prior suite)
- Anti-false-pass scan: **PASS** (no `or True`, no bare `except: pass`)
- `curl http://localhost:11434/api/ps` → `{"models":[]}`
- Hearsay `tests/test_pipeline_writer.py`: not present at expected path this checkout (no re-run); not required for Round 12 fusion diagnostics

---

## How to Use Tonight

After class, open:

```text
sessions/<session>/realtime_fusion_status.jsonl
```

Each line = one realtime fusion attempt:

- **No lines at all** → pending was empty whenever fusion ticked (normal if nothing unfused, or fusion thread not started).
- **Many lines with `result: success`** → realtime fusion consuming pending.
- **Lines with `result: skip`** → model answered skip; pending intentionally retained (safe for Final Fusion).
- **Lines with `result: invalid_json` / `timeout` / `exception` / `failure`** → realtime fusion failing that tick; **pending retained**; session stays RUNNING; Final Fusion still gets the tail.
- Rising `attempt_id` + repeated non-success results = “一直在 skip/失败”, not “没 pending”.

Counts + `input_chars` + `latency_ms` help distinguish “empty input” vs “model hung/failed” without logging prompts/responses.

---

## Suitability for Tonight

- **Safe to ship:** observational only; failure paths cannot kill capture or Final Summary.
- **Does not claim** realtime Fusion is healthy — report only classifies outcomes.
- If status shows persistent `exception`/`timeout`, that is evidence for a later model/timeout round (out of scope here).

---

## Files

- `app/course_session/note_engine.py`
- `app/course_session/session.py`
- `app/course_session/storage.py`
- `test_stability.py`
- `docs/ROUND12_REALTIME_FUSION_DIAGNOSTICS.md` (this file)
