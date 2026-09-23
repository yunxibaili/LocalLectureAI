# Round 13: Realtime Fusion Root Cause Fix

**Date:** 2026-09-23  
**Base:** `ca724d0` (Round 12)  
**Goal:** Realtime Fusion on tonight's New Oriental calculus live class must succeed on at least one real batch; `live_notes.md` must update live. Pending data safety and the already-PASS lifecycle (Whisper / Visual / Final Fusion / Final Summary / 27B / `/api/ps`) must stay unchanged.

**Honesty:** This round fixes the realtime path root cause with real probes + one real-session replay. It does **not** claim a full live class run. Vision full-description mode and OBS window selection remain deferred.

---

## 1. Root Cause

Four independent defects, all confirmed against real Ollama (`qwen3-vl:8b` / `qwen2.5:7b`):

| # | Defect | Evidence |
|---|--------|----------|
| A | `qwen3-vl:8b` is a **thinking** model: with `format=json`, answer often lands in `message.thinking` while `content=""`; `done_reason=length` when `num_predict` is burned by thinking. QLens `chat_text()` only reads `content` → empty → `FUSE_EMPTY` / `invalid_json`. | Real probe: `content=0`, answer in thinking, stage=direct after harvest |
| B | Without `format=json`, latency 45–68s/req and intermittent empty content. | Real probe |
| C | LaTeX invalid JSON escapes (`\c`, `\cdot`, …) fail `json.loads` (also hit `qwen2.5:7b`). | Real probe + Test AX |
| D | **Window consume bug:** `_fuse_once` snapshotted then identity-consumed the **entire** pending list, but `build_fusion_prompt` only included last 40 transcript / last 6 visual → events beyond the window were silently dropped. | Session code + Test AV |

Concurrent vision+fusion on the same model also caused ReadTimeout 62s + empty content (secondary; not changed this round beyond documented risk).

---

## 2. Round 12 Diagnostic Integration

Round 12 added structured status only (`timestamp, attempt_id, pending_*, input_chars, result, latency_ms, exception_type, error_message`). Round 13 **extends** that same status line; it does not replace it.

New status keys (sizes/enums only — still no full prompt/response):

- `model`, `prompt_chars`, `transcript_chars`, `visual_chars`, `response_chars`
- `parse_stage` (`empty|direct|fence|brace|escape_repaired|brace_escape_repaired|invalid|timeout|exception|empty_input|empty_response`)
- `http_status`, `fused_transcript_count`, `fused_visual_count`

Round 12 tests AI–AT were **re-bound** from `oc.chat_text` to `note_engine_mod.chat_fusion` for the realtime path only. Final-fusion tests (Q/R/S/W-final/AA–AH) still patch QLens `chat_text`. Round 12 classifications (timeout / invalid_json / exception / skip / failure / empty) and pending-preservation semantics are unchanged.

---

## 3. Fix

### `app/course_session/note_engine.py`

- **`parse_fusion_json(raw) -> (dict|None, stage)`** — staged parse: direct → fence → brace → invalid-escape repair (`_INVALID_JSON_ESCAPE_RE`).
- **`chat_fusion(...)`** (module-level): posts Ollama with `format=json`, `think=False`, `num_predict=REALTIME_FUSION_NUM_PREDICT` (2048), harvests `content` **or** `thinking`, records `http_status` / `response_chars` / `response_source` on `diag`. Does **not** modify QLens.
- **`fuse()`** calls `chat_fusion` (not `chat_text`); fills `last_fuse` with Round 12+13 diag.
- **`_chat_fusion`** retry loop still 2 attempts on non-JSON; tests patch `note_engine.chat_fusion`.
- **`fuse_final` / `_chat_fusion_final`** still use QLens `chat_text` (FINAL_MODEL path; Round 8 `think=False` + `timeout>=300` unchanged). Single definition only.

### `app/course_session/session.py`

- `_fuse_once`: prompt window = last `RECENT_TRANSCRIPT_EVENTS` (40) / `RECENT_VISUAL_EVENTS` (6); **success identity-consumes only that window**; backlog older than the window stays pending for later ticks / Final Fusion.
- Status record includes Round 13 diagnostic keys.

### `app/course_session/prompts.py`

- `build_fusion_prompt`: `_cap_join` caps CourseState history lists (topic 200 chars; concepts/formulas/emphasis/unresolved bounded) so long classes cannot push `num_predict` into thinking-only truncation.

### `app/course_session/settings.py`

- `REALTIME_FUSION_NUM_PREDICT` env default **2048** (was implicit 6144 on final path only; realtime was burning thinking budget).

### Model decision

Continue `qwen3-vl:8b` as `REALTIME_FUSION_MODEL`. Do **not** swap to `qwen2.5:7b` (12GB VRAM dual-model risk). Real probes show the same model succeeds 3/3 on early/mid/late real windows once `format=json` + thinking harvest is used.

---

## 4. Tests

`python test_stability.py` → **STABILITY_UNIT_TEST PASS** (EXIT=0).

| Block | Coverage |
|-------|----------|
| **E, M, X, Y, Z, W** (existing, rebased) | fuse=False / failure pending / mid-fuse insert / Final tail / skip+success now mock `chat_fusion` |
| **AI–AT** (Round 12) | re-based to `chat_fusion`; classifications + status keys green |
| **AU** | timeout → invalid_json → success; pending kept until success; `attempt_id` 1..3; call count `>=3` (internal retry may call 4 times) |
| **AV** | window consume: backlog 55 → consume 40, 15 left; status `pending=55`, `fused=40` |
| **AW** | Round 13 status keys present; privacy (no transcript/prompt/response body) |
| **AX** | fence / latex `\cdot` escape repair / invalid / empty stages |
| **AY** | `chat_fusion` thinking harvest via mock `requests.post` |
| **AZ** | `fuse_final` body does not call `chat_fusion`; still has `chat_text` helper |

Anti-false-pass scan: **PASS** (no `or True`, no bare `except Exception: pass`, exit 1 on FAIL).

Hearsay `tests/test_pipeline_writer.py`: **ALL CHECKS PASSED** (EXIT=0).

`py_compile` on changed modules + replay script: **OK**.

---

## 5. Real Replay

```text
python scripts/round13_replay_fusion.py
```

Source: real session `sessions/2026-09-23_13-04-21` (738 events / 10 visual / live_notes 3273 B).

```text
SOURCE: 80 transcript / 10 visual loaded
attempt 1: result=success pending=80 fused=40 stage=direct http=200
           lat=7578ms resp_chars=512 left=40
REPLAY PASS: live_notes_bytes=2553 course_state_lines=1 updates=1
```

Output session: `sessions/2026-09-23_14-32-27`

- `realtime_fusion_status.jsonl`: 1 line, `result=success`
- `live_notes.md`: **2553 bytes**, non-empty (topic 极限与连续, formulas, emphasis, visual lines)
- `course_state.jsonl`: 1 update
- Window semantics: 80 pending → fused 40 → 40 backlog retained

Source session was **not** mutated.

---

## 6. Data Safety

- Snapshot → fuse → **success identity-consumes only the prompt window**.
- timeout / invalid_json / exception / skip / failure: **pending retained** (AU, AI–AT, X).
- Status write is best-effort; never fails fusion; never embeds full prompt/response (AP, AW).
- Fusion failure never sets `SessionState.FAILED` and never stops capture (AO).

---

## 7. Realtime Fusion Status

Real replay line (privacy-safe sizes only):

```json
{
  "result": "success",
  "model": "qwen3-vl:8b",
  "pending_transcript_count": 80,
  "fused_transcript_count": 40,
  "parse_stage": "direct",
  "http_status": 200,
  "latency_ms": 7578,
  "prompt_chars": 3893,
  "response_chars": 512
}
```

---

## 8. Final Lifecycle (unchanged)

- Final Fusion still QLens `chat_text` on FINAL_MODEL, `think=False`, explicit `timeout>=300` (Round 8; Test AH, AZ).
- Final Summary / 27B exclusive load / unload / `/api/ps` gate: Round 5–8 tests J, J2, Q–AG still PASS.
- Whisper / Visual capture core / QLens / Hearsay bodies: not modified.
- After unload probe this round: `curl /api/ps` → `{"models":[]}` (model was only resident briefly during replay).

---

## 9. Remaining Risks / Deferred

| Item | Status |
|------|--------|
| Vision full-description mode | Deferred (explicitly out of Round 13 scope) |
| OBS window selection | Deferred (recorded for later) |
| Concurrent vision+fusion same-model contention | Documented; not redesigned this round |
| Full live class end-to-end run tonight | Not claimed; one real-batch replay only |
| `num_predict=2048` still thinking-sensitive | Mitigated by `think=False` + format=json + `_cap_join`; re-probe if length truncation returns |

---

## 10. Files

- `app/course_session/note_engine.py` — `chat_fusion`, `parse_fusion_json`, fuse diag
- `app/course_session/session.py` — window slice + window consume + status keys
- `app/course_session/prompts.py` — `_cap_join` state caps
- `app/course_session/settings.py` — `REALTIME_FUSION_NUM_PREDICT`
- `test_stability.py` — AU–AZ + mock rebase (W/E/M/X/Y/Z, AI–AT)
- `scripts/round13_replay_fusion.py` — real-session replay driver
- `docs/ROUND13_REALTIME_FUSION_ROOT_CAUSE_REPORT.md` (this file)

---

## 11. Commit

See git history for `Round 13: realtime fusion root cause fix (format=json + thinking harvest + window consume)`.
