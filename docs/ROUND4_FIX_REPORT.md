# Round 4 Fix Report — Model Role & Phase Separation

**Base commit:** `e216833`  
**Goal:** Fix architecture semantics (not more `FUSION_MODEL=27B` patches).

## Model roles (final)

```
VISION_MODEL=qwen3-vl:8b
REALTIME_FUSION_MODEL=qwen3-vl:8b
FINAL_MODEL=qwen38-27b-main:latest
```

- Removed ambiguous `FUSION_MODEL` config.
- Ollama tag on this machine is `qwen3-vl:8b` (not `*-instruct-q4_K_M`).
- Project does not set `OLLAMA_MODELS`.

## Changes

### 1. Settings / roles
- `settings.py`: `REALTIME_FUSION_MODEL` (default `= VISION_MODEL`).
- `validate_model_config()`: rejects empty realtime names, any realtime name
  containing `27b`, `REALTIME == FINAL`, or realtime == `FINAL_MODEL`.
- `describe_model_roles()` for startup print.

### 2. Realtime fusion never 27B
- `note_engine.py`: default model = `REALTIME_FUSION_MODEL`.
- Session start logs fusion model; Test I asserts 8B.

### 3. Stop order (strict)
```
stop workers → join → release realtime models → /api/ps verify
  → only then generate_final_summary (FINAL_MODEL)
```
- Removed realtime `_fuse_once()` from `stop_fusion` block (no final fusion
  before release).
- Added explicit `can_load_final_model()` gate after release before FINAL path.

### 4. Preflight fail-closed
- `/api/ps` `None` → refuse start (registry empty cannot override API failure).

### 5. Release driven by `/api/ps`
- Targets = managed realtime names **present in `/api/ps`**, even if local
  registry is empty (Test L).
- Never unloads unrelated Ollama models.

### 6. Startup exception → unified cleanup
- Audio/visual/fusion start failures call `_startup_cleanup` → `_cleanup_core`
  (stop started workers + release + verify). Test O.

### 7. Fuse registration honesty
- `session._fuse_once`: `mark_model_loaded` only when `fuse()` returns True.
- `note_engine.fuse` already marks only on valid delta (skip/invalid → no mark).
- Test M.

### 8. start_course.ps1
- Removed `$env:FUSION_MODEL = $env:VISION_MODEL` override.
- Sets `REALTIME_FUSION_MODEL` default only; prints all three roles.
- Does not invent hidden shell semantics over Python settings.

## Tests added (I–P)

| Test | Asserts |
|---|---|
| I | Realtime roles are `qwen3-vl:8b`; validate accepts defaults; NoteEngine uses RT model |
| J | Source order in `_cleanup_core`: join < release < ps gate < final summary; no realtime `_fuse_once` during stop |
| K | `/api/ps` failure → `preflight_cleanup` False (fail-closed) |
| L | registry empty + `/api/ps` has VLM → unload still posted |
| M | `_fuse_once` with fuse=False → no `mark_model_loaded` |
| N | `REALTIME==FINAL` 27B or any `27b` realtime name → `validate_model_config` raises |
| O | startup exception → `_cleanup_core` with `release_models=True`, `wait_final_summary=False` |
| P | audio fatal cleanup → `/api/ps` free of `qwen3-vl` |

Plus role snapshot print of the three env values.

## Real verification

| Check | Result |
|---|---|
| `/api/ps` empty before tests | `{"models":[]}` |
| `qwen3-vl:8b` present in `/api/tags` | yes |
| `qwen38-27b-main:latest` present | yes |
| `test_stability.py` (A–P) | see terminal EXIT |
| AST parse `app/course_session/*.py` | 12 files |
| Hearsay `test_pipeline_writer.py` | ALL CHECKS PASSED |

Full GPU e2e (real 8B vision call + stop + load 27B + summary) is environment-
gated (screen/audio); unit + static order checks cover the lifecycle contract.

## Explicit non-goals (unchanged)

No Bonsai 2, no new VLM, no cloud, no RAG, no UI redesign, no QLens/Hearsay
rewrite, no large refactor.

## Status

Committed and pushed. **Stop** — await independent Codex Round 4 review.
