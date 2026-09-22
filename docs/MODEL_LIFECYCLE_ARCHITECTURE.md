# Model Lifecycle Architecture (Round 4)

RTX 5070 **12GB** requires strict time-slicing between a realtime 8B VLM and
an exclusive post-class 27B model. This document is the authority for model
roles; code and scripts must not invent conflicting semantics.

## Model roles

| Role | Env / setting | Default | When loaded | When forbidden |
|---|---|---|---|---|
| Vision (realtime) | `VISION_MODEL` | `qwen3-vl:8b` | Realtime screen analysis | Never during FINAL phase alongside 27B |
| Realtime fusion | `REALTIME_FUSION_MODEL` | `= VISION_MODEL` → `qwen3-vl:8b` | Incremental note fusion in realtime loop | Never `FINAL_MODEL` / any 27B |
| Final (post-class) | `FINAL_MODEL` | `qwen38-27b-main:latest` | Final fusion + summary **only after** realtime release + `/api/ps` clean | Never while realtime VLM/Whisper workers alive |

There is **no** `FUSION_MODEL` config anymore. Shell scripts must not
redefine it. Python `settings.py` owns role resolution.

## Phase state machine

```
IDLE
 ↓
PREFLIGHT          # validate_model_config + preflight_cleanup (/api/ps fail-closed)
 ↓
REALTIME           # Whisper + qwen3-vl:8b only; never qwen38-27b
 ↓ stop
STOPPING           # join all realtime workers
 ↓
REALTIME_CLEANUP   # release VISION/REALTIME_FUSION via /api/ps
 ↓
VERIFY_NO_REALTIME # /api/ps must not show 8B VLM
 ↓
FINAL              # load qwen38-27b → final fusion + summary
 ↓
FINAL_CLEANUP      # optional unload of 27B
 ↓
STOPPED

any cleanup verification failure → FAILED
FAILED must not continue into FINAL (no 27B load)
```

## Why time-slicing on 12GB

- `qwen3-vl:8b` peak ≈ **8.6GB** VRAM during vision calls.
- `qwen38-27b-main` peak ≈ **11.7GB** VRAM during final summary.
- Concurrent residency OOMs the card.

Therefore realtime and final phases are mutually exclusive.

## `/api/ps` as hard gate

Authoritative source for “what is resident” is Ollama `GET /api/ps`, **not**
the local process registry (registry only records what *this process thinks*
it loaded).

Gates:

1. **Preflight**: `/api/ps` query fail (`None`) → **fail-closed**, refuse start.
2. **Release**: unload only managed realtime names (`VISION_MODEL`,
   `REALTIME_FUSION_MODEL`); verify they disappear; still present after
   retries → `CLEANUP_FAILED`.
3. **Final load**: workers all dead **and** `can_load_final_model()` true
   (no realtime models in `/api/ps`) before any `FINAL_MODEL` call.
4. **Startup exception**: always `_cleanup_core` (stop started workers +
   release + verify), never a bare `audio.stop()`.

## What is forbidden

- Realtime loop calling `qwen38-27b-main`.
- Loading `FINAL_MODEL` while any realtime worker is alive.
- Loading `FINAL_MODEL` when `/api/ps` still shows realtime models.
- Preflight succeeding when `/api/ps` cannot be queried.
- `mark_model_loaded` on fuse failure / skip / exception.
- Shell override of `FUSION_MODEL` or any hidden realtime=27B mapping.
- Inventing model tags that do not exist on the machine (use `qwen3-vl:8b`).

## Extension point

Replacing the post-class model (e.g. future Bonsai 2) only changes
`FINAL_MODEL`. Realtime capture chain (`WASAPI → Whisper`, `Screen → QLens →
qwen3-vl`) stays unchanged.

## Runtime print

At session start the app logs:

```
VISION_MODEL=qwen3-vl:8b
REALTIME_FUSION_MODEL=qwen3-vl:8b
FINAL_MODEL=qwen38-27b-main:latest
```

`start_course.ps1` only sets defaults into the environment and prints the
same roles; it must not override Python settings semantics.
