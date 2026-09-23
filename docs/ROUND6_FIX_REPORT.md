# Round 6 Fix Report — pending honesty + final-fusion failure honesty

**Base commit:** `3dfbc21` (Round 5)  
**Goal:** Fix Codex Round 5 FAIL findings only — (1) realtime fusion failure must not drop pending; (2) Final Fusion failure must not produce a full `final_summary.md`. No architecture changes.

## 1. Realtime pending consume rule

**Problem:** `_fuse_once()` snapshotted then **cleared** `_unfused_*` **before** `fuse()`. Invalid JSON / exception / timeout left pending empty, so Final Fusion never saw the tail.

**Rule:**

| Outcome | Pending |
|---|---|
| SUCCESS (fuse applied delta) | consume **only** the snapshot batch (event identity) |
| SKIP / NO-OP (skip=True, empty lines) | **keep** pending (attempt ≠ success) |
| FAILURE (invalid JSON, exception, timeout, Ollama/network) | **keep** pending |

- No `clear()` on the failure/skip path.
- Success path filters by `e is x` identity — never a full clear (events inserted **during** fuse stay).
- No new ID system; no new lock.

**Code:** `session.py` `_fuse_once()` — snapshot `list(...)`, `fuse()`, then on truthy return identity-filter pending.

**Status line:** failure/skip appends `（skip/失败，pending 保留）`.

## 2. Final Fusion failure must not write full summary

**Problem:** `run_final_phase()` always attempted `generate_final_summary` after a failed Final Fusion, so a full `final_summary.md` could appear beside an incomplete fused state.

**Rule:**

| Fusion status | Full `final_summary.md` | Cleanup (`release_final_model` + `/api/ps`) |
|---|---|---|
| SUCCESS | allowed | always (finally) |
| NO-OP | allowed | always (finally) |
| FAILURE (incl. exception / timeout via `fuse_final_status_safe`) | **forbidden** | always (finally) |

On FAILURE `run_final_phase`:
1. appends `final fusion failed` and `final summary skipped: final fusion failure`
2. writes existing `storage.write_partial_notes(reason=...)` → `partial_notes.md`
3. returns `summary_path` = partial path (not `final_summary.md`)
4. `finally` still runs `release_final_model()` + resident verify

Session `_cleanup_core` on `fusion_status == "failure"`:
- forces `success = False` (existing formula kept)
- if partial missing, writes one as a safety net
- status shows `部分完成: …/partial_notes.md` (never `完成: …/final_summary.md` on failure)

**Residual/stale `final_summary.md`:** `SessionStorage` creates a **fresh session dir** per course; failure tests assert `final_summary_path` is absent for that run. Failure path never returns a path ending in `final_summary.md`.

## 3. New tests (Round 6)

| Test | Asserts |
|---|---|
| **X** | fuse failure keeps pending identity; no registry mark; no state update; source has identity consume (`any(e is x`), no pre-fuse clear |
| **Y** | success consumes only snapshot; event inserted **during** fuse remains pending; delta applied |
| **Z** | realtime failure tail (`TAIL_TOPIC`/`TAIL_FACT`) still pending → drained into `run_final_phase(pending_transcripts=…)` Final Fusion input |
| **AA** | fusion failure → `generate_final_summary` not called; no `final_summary.md`; errors include summary skipped; fresh session has no pre-existing summary |
| **AB** | `fuse_final` exception → `FUSION_FAILURE`; no summary |
| **AC** | fuse timeout (`TimeoutError`) → `FUSION_FAILURE`; no summary |
| **AD** | fusion failure → `partial_notes.md` exists, non-empty; `summary_path` is partial; no full summary |
| **AE** | fusion failure → `release_final_model` still called in finally; registry clean |
| **AF** | fusion failure → mocked `/api/ps` free of FINAL_MODEL after release; no full summary |
| **AG** | fusion **success** → summary generated as `final_summary.md`; clean errors; FINAL_MODEL released |

Anti-false-pass: no `or True`, no bare `except Exception: pass`, exit 1 on FAIL (existing needles).

## 4. Mutation checks (not committed as product code)

Temp script mutated official sources, ran `test_stability.py`, restored files:

| Mutation | Expected |
|---|---|
| **X** — `clear()` pending before `fuse()` | suite **FAIL** |
| **AA** — skip failure guard (allow summary on failure) | suite **FAIL** |
| **AE** — skip `release_final_model` in finally | suite **FAIL** |
| **AF** — no-op release (leave 27B “resident”) | suite **FAIL** |

Restore verified: suite back to PASS after each mutation.

## 5. Verification summary

| Check | Result |
|---|---|
| `test_stability.py` | `STABILITY_UNIT_TEST PASS`, exit **0** (includes X/Y/Z/AA–AG) |
| AST `py_compile` (session/final_summary/note_engine/test_stability) | `AST_OK` |
| Hearsay `tests/test_pipeline_writer.py` | `ALL CHECKS PASSED` / `HEARSAY_OK` |
| `/api/ps` at Round 6 end | `{"models":[]}` |
| Mutation X/AA/AE/AF | each **FAIL as expected**, then restore PASS |

## 6. ROUND5 report integration claim (cleanup)

`docs/ROUND5_FIX_REPORT.md` §7: the Round 5 “10/10 integration” script (`_round5_integration.py`) was **never committed**. Wording now states those step counts are **not reproducible evidence** and are **not claimed as tests**; Round 6 authority is `test_stability.py` + AST + Hearsay + live `/api/ps`.

## Files changed

- `app/course_session/session.py` — `_fuse_once` snapshot/identity consume; fusion-failure status/partial safety net
- `app/course_session/final_summary.py` — `run_final_phase` failure → skip summary, write partial, finally still release+ps
- `test_stability.py` — Test X/Y/Z + AA–AG
- `docs/ROUND5_FIX_REPORT.md` — non-reproducible integration claim reworded (option B)
- `docs/ROUND6_FIX_REPORT.md` — this document

## Out of scope (not done, by design)

Architecture/lifecycle refactor, new models, Bonsai 2, VLM/ASR, cloud AI, RAG, UI redesign, QLens/Hearsay rewrite, performance tuning, major Prompt changes.
