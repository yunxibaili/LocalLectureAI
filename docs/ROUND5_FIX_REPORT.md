# Round 5 Fix Report — Final Fusion + FINAL_MODEL Cleanup

**Base commit:** `d96b188` (Round 4)  
**Goal:** Fix Codex Round 4 findings only — real Final Fusion, strict final-phase order, always release FINAL_MODEL after final work, fix skip=True / Test L / Test J honesty. No scope expansion.

## 1. Final Fusion (was missing)

**Problem:** Round 4 stopped at `can_load_final_model` + summary; pending realtime fusion work never ran a dedicated final pass on FINAL_MODEL.

**Fix:**
- `note_engine.py`: `NoteEngine.fuse_final(transcripts, visuals, model=FINAL_MODEL)`
  - No pending events → `FUSION_NOOP` (does **not** call the model).
  - Empty lines after extract → `FUSION_NOOP`.
  - Chat fail / non-JSON → `FUSION_FAILURE` (no registry mark).
  - `skip=True` → `FUSION_NOOP` (no registry mark).
  - Applied delta → `FUSION_SUCCESS` + `mark_model_loaded(model)` only.
  - Does **not** mutate `self.model` (realtime role stays `REALTIME_FUSION_MODEL`).
- `final_summary.py`: `run_final_phase(...)` returns `(fusion_status, summary_path, errors)` and **always** runs Final Fusion before Final Summary when pending work exists.
- Session `_cleanup_core` drains `_unfused_transcripts` / `_unfused_visuals` under lock and feeds them to `run_final_phase`.

## 2. Final phase order (authoritative)

```
stop workers → join → verify workers dead
  → release VISION_MODEL / REALTIME_FUSION_MODEL
  → /api/ps confirm realtime gone
  → Final Fusion (pending → CourseState / live notes; none → no-op)
  → Final Summary (final_summary.md)
  → release FINAL_MODEL
  → /api/ps confirm FINAL_MODEL gone
  → STOPPED
```

Forbidden: loading 27B while realtime VLM/workers alive; skipping Final Fusion with pending work; leaving 27B resident after summary.

## 3. FINAL_MODEL cleanup

- `release_final_model()` unloads **only** `FINAL_MODEL` (never unrelated Ollama models).
- `run_final_phase` uses `try: fusion/summary finally: release_final_model + /api/ps`.
- Unload / verify failure → errors non-empty → session **not** STOPPED success (`CLEANUP_FAILED` → `StopResult.success=False`, state `FAILED`).
- `final_summary.md` from a successful summary may remain; cleanup failure still fails overall success.
- Exceptions from fusion/summary are **not** swallowed; finally still releases.

## 4. skip=True registry honesty

| Outcome | `fuse()` / `fuse_final()` return | `mark_model_loaded` |
|---|---|---|
| No pending / empty lines | `FUSION_NOOP` (fuse: False) | no |
| `skip=True` | `FUSION_NOOP` (fuse: False) | no |
| Invalid JSON / exception | `FUSION_FAILURE` (fuse: False) | no |
| Applied delta | `FUSION_SUCCESS` (fuse: True) | **yes** |

`Session._fuse_once` only registers when fuse returns True.

## 5. Test L / Test J fixes

- **Test L:** asserts unload POST body `payload["model"] == "qwen3-vl:8b"` exactly; no unrelated model unloads.
- **Test J:** behavioral order via `order_events` — `join < release < ps < final_fusion < final_summary < final_release`; source checks for `release_realtime_models(` / `can_load_final_model(` / `run_final_phase(`; no realtime `_fuse_once` during stop.
- **Test J2:** `can_load_final_model` / ps gate fails → `final_load` never runs, `success=False`, error contains `ps gate`.
- Source comment in `session.py` no longer embeds `_fuse_once` as a scan self-match.

## 6. Tests Q–V / W (Round 5 additions)

| Test | Asserts |
|---|---|
| Q | Final Fusion runs on pending events with FINAL_MODEL; realtime role unchanged; success marks only FINAL_MODEL; state persisted |
| R | No pending → explicit no-op; 27B chat not called; summary still runs; release still attempted |
| S | Fusion failure → `FUSION_FAILURE`, errors include fusion failure, release + ps still run, not fake success |
| T | Summary success order: `ps_before → summary → unload`; clean errors; file kept |
| U | Summary exception propagates (not swallowed); `finally` still releases FINAL_MODEL |
| V | FINAL_MODEL unload failure → cleanup failure / not full success; summary file may exist |
| W | skip=True does not register; success does; final registry independent of realtime |
| J2 | ps gate fail blocks final phase entirely |

Anti-false-pass scan: no always-true `or True` asserts; no bare `except Exception: pass`; exit 1 on any FAIL.

## 7. Real Ollama verification (this machine)

A temporary integration script was executed during Round 5 and was **not**
committed to the repository. Therefore those "10/10" step counts are **not
reproducible evidence** for Round 6 reviewers and are **not claimed as tests**.

What was observed ad-hoc (not re-runnable from the repo):
- `/api/ps` empty before and after
- `qwen3-vl:8b` realtime call + release → ps empty
- `qwen38-27b` fuse_final/summary + release → ps empty
- models never resident together

**Round 6 authority:** `test_stability.py` (Test X/Y/Z/AA–AG) + AST +
Hearsay `test_pipeline_writer.py` + current `/api/ps` = `{"models":[]}`.

## 8. Verification summary

| Check | Result |
|---|---|
| `test_stability.py` | `STABILITY_UNIT_TEST PASS`, exit **0** |
| AST `py_compile` (4 files) | `AST_OK` |
| Hearsay `test_pipeline_writer.py` | `ALL CHECKS PASSED` / `HEARSAY_OK` |
| `/api/ps` at Round 5 end | `{"models":[]}` |

## Files changed

- `app/course_session/note_engine.py` — `FUSION_*`, `fuse_final`, skip honesty, shared line builders
- `app/course_session/final_summary.py` — `_release_named_models`, `release_final_model`, `run_final_phase` (fusion→summary→finally release+ps)
- `app/course_session/session.py` — drain pending under lock, track fusion/release status, success formula
- `docs/MODEL_LIFECYCLE_ARCHITECTURE.md` — Round 4→5 lifecycle order + FINAL_CLEANUP required
- `test_stability.py` — Test L/J honesty, Test Q–W/J2, Anti-false-pass needles
- `docs/ROUND5_FIX_REPORT.md` — this document

## Out of scope (not done, by design)

New VLM, Bonsai 2, cloud AI, RAG, UI redesign, new course features, QLens/Hearsay rewrites, large architecture refactors, new models, performance tuning, major Prompt changes.
