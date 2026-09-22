"""Post-class high-quality summary via qwen38-27b (exclusive GPU)."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .prompts import FINAL_SUMMARY_SYSTEM, build_final_summary_prompt
from .settings import (
    FINAL_MODEL,
    NUM_CTX_FINAL,
    REQUEST_TIMEOUT,
    UNLOAD_RETRY_COUNT,
    UNLOAD_RETRY_DELAY_S,
    VISION_MODEL,
    FUSION_MODEL,
    CleanupStatus,
    forget_loaded_models,
    is_model_resident,
    list_loaded_models_via_api,
    loaded_models,
    mark_model_loaded,
    ollama_base_url,
)
from .storage import SessionStorage

log = logging.getLogger(__name__)


def release_realtime_models(exclude: set[str] | None = None) -> tuple[bool, list[str]]:
    """Release tracked realtime models and verify via /api/ps.

    Hard constraints:
    - Only touches models this process tracked (loaded_models() registry)
      OR explicitly declared managed models (VISION_MODEL, FUSION_MODEL).
    - Never touches other Ollama models (do not kill unrelated processes).
    - unload -> /api/ps -> retry -> if still resident after retries = FAIL.

    Returns (success, error_messages).
    """
    import requests

    exclude = exclude or set()
    # Collect targets: registry ∪ declared-managed ∩ (not in exclude)
    managed = {VISION_MODEL, FUSION_MODEL, FINAL_MODEL}
    registry_targets = loaded_models() - exclude
    # Only try to unload models we're responsible for
    targets = sorted((registry_targets | (managed & set(loaded_models() | managed))) - exclude)
    # If a model is in registry but not in managed, still respect registry
    # (we may have loaded it); if not in registry but is managed, only unload
    # if it appears in /api/ps (might be leftover from previous process).
    # But constraint 3: preflight must not kill other processes' models.
    # So for preflight, only touch managed models that WE declare.
    # For release during stop, use registry ∪ managed that we tracked.

    # Simplify: targets = models in registry (what we loaded) minus exclude,
    # plus managed models only if they're in registry OR we're doing preflight.
    # Actually for release_realtime_models called during stop: use registry.
    targets = sorted(loaded_models() - exclude)
    if not targets:
        # Also check managed models that might be resident from prior session
        # but only if they're not FINAL_MODEL (we never auto-unload FINAL)
        pass

    errors: list[str] = []
    try:
        base = ollama_base_url()
    except Exception as e:
        return False, [f"base_url: {e}"]

    # Check what's actually resident before we start
    resident_before = list_loaded_models_via_api()

    for name in targets:
        if not resident_before:
            # cannot query /api/ps — still attempt unload
            pass
        elif name not in resident_before and not any(
            name in r or r in name for r in resident_before
        ):
            # not resident — skip unload
            continue

        # attempt unload with retries
        success = False
        for attempt in range(UNLOAD_RETRY_COUNT):
            try:
                r = requests.post(
                    f"{base}/api/generate",
                    json={"model": name, "keep_alive": 0},
                    timeout=10,
                )
                if r.status_code >= 400:
                    errors.append(f"{name}: HTTP {r.status_code} (attempt {attempt+1})")
                    if attempt < UNLOAD_RETRY_COUNT - 1:
                        time.sleep(UNLOAD_RETRY_DELAY_S)
                        continue
                else:
                    # verify via /api/ps
                    time.sleep(UNLOAD_RETRY_DELAY_S)
                    resident = is_model_resident(name)
                    if resident is False:
                        success = True
                        forget_loaded_models({name})
                        log.info("released model %s (verified via /api/ps)", name)
                        break
                    elif resident is True:
                        errors.append(f"{name}: still resident after unload (attempt {attempt+1})")
                        if attempt < UNLOAD_RETRY_COUNT - 1:
                            time.sleep(UNLOAD_RETRY_DELAY_S)
                            continue
                    else:
                        # resident is None — cannot verify, assume success for now
                        # but mark that we can't be sure
                        success = True  # best-effort if cannot query
                        forget_loaded_models({name})
                        log.warning("released %s but cannot verify via /api/ps", name)
                        break
            except Exception as e:
                errors.append(f"{name}: {e} (attempt {attempt+1})")
                if attempt < UNLOAD_RETRY_COUNT - 1:
                    time.sleep(UNLOAD_RETRY_DELAY_S)

        if not success:
            # final check
            resident_final = is_model_resident(name)
            if resident_final is True:
                errors.append(f"{name}: CONFIRMED STILL RESIDENT after {UNLOAD_RETRY_COUNT} attempts")

    # Final verification: if any managed/registry model still resident -> FAIL
    final_resident = list_loaded_models_via_api()
    if final_resident is None:
        # cannot query /api/ps — conservative failure when we had targets
        if targets:
            return False, errors + ["cannot verify /api/ps — treating as cleanup failure"]
        return True, errors
    still_resident = []
    for name in targets:
        if name in final_resident or any(name in r for r in final_resident):
            still_resident.append(name)
    if still_resident:
        return False, errors + [f"STILL RESIDENT: {', '.join(still_resident)}"]
    return True, errors


def can_load_final_model() -> tuple[bool, str]:
    """Gate: may we load FINAL_MODEL (qwen38-27b)?

    Hard constraints:
    1. No worker threads still alive (caller must check this separately)
    2. /api/ps must show realtime models released
    3. cleanup must have succeeded

    Returns (allowed, reason).
    """
    # Check /api/ps for realtime models (None = cannot verify; empty set = clean)
    resident = list_loaded_models_via_api()
    if resident is None:
        return False, "cannot verify /api/ps (query failed) — refuse to load 27B"

    # Realtime models that must NOT be resident
    realtime = {VISION_MODEL, FUSION_MODEL}
    # Also any registry models except FINAL_MODEL
    realtime |= (loaded_models() - {FINAL_MODEL})

    conflicts = []
    for rt in realtime:
        if not rt:
            continue
        for r in resident:
            if rt == r or rt in r or r in rt:
                conflicts.append(rt)
                break
    if conflicts:
        return False, f"realtime models still resident: {', '.join(conflicts)}"

    # Check if FINAL_MODEL already resident (from prior session) — that's OK to
    # proceed since we're about to load it anyway; but if we're in preflight
    # and it's resident, we should unload first (handled by preflight_cleanup)
    return True, "ok"


def preflight_cleanup() -> tuple[bool, str]:
    """Called at CourseSession.start() BEFORE any workers start.

    Unloads leftover FINAL_MODEL (and any tracked realtime models) from a
    previous session. Only touches models THIS app declares as managed.

    Returns (ok, message). If not ok, session start must abort.
    """
    from .settings import FINAL_MODEL as _FM

    # Only check managed models — don't unload random Ollama models
    managed = {_FM, VISION_MODEL, FUSION_MODEL}

    resident = list_loaded_models_via_api()
    if resident is None:
        # cannot query — conservative: if FINAL_MODEL in registry, refuse
        if _FM in loaded_models():
            return False, "Previous final model may still be resident (cannot verify /api/ps)."
        # if we can't query and nothing in registry, allow (first run / Ollama down)
        return True, "no /api/ps data, registry empty — proceeding"

    # Check if any managed model (especially FINAL_MODEL) is resident
    to_unload = []
    for name in managed:
        if not name:
            continue
        for r in resident:
            if name == r or name in r or r in name:
                to_unload.append(name)
                break

    if not to_unload:
        return True, "no managed models resident"

    # Unload only the managed models that are resident
    import requests

    try:
        base = ollama_base_url()
    except Exception as e:
        return False, f"base_url error: {e}"

    errors = []
    for name in to_unload:
        for attempt in range(UNLOAD_RETRY_COUNT):
            try:
                r = requests.post(
                    f"{base}/api/generate",
                    json={"model": name, "keep_alive": 0},
                    timeout=10,
                )
                if r.status_code >= 400:
                    errors.append(f"{name}: HTTP {r.status_code}")
                    time.sleep(UNLOAD_RETRY_DELAY_S)
                    continue
                time.sleep(UNLOAD_RETRY_DELAY_S)
                check = is_model_resident(name)
                if check is False:
                    forget_loaded_models({name})
                    break
                elif check is True:
                    errors.append(f"{name}: still resident (attempt {attempt+1})")
                    if attempt == UNLOAD_RETRY_COUNT - 1:
                        return False, (
                            f"Previous final model is still resident: {name}. "
                            + "; ".join(errors)
                        )
                    time.sleep(UNLOAD_RETRY_DELAY_S)
                else:
                    # cannot verify
                    break
            except Exception as e:
                errors.append(f"{name}: {e}")
                time.sleep(UNLOAD_RETRY_DELAY_S)

    # Final verify
    resident_final = list_loaded_models_via_api()
    if resident_final is None:
        if _FM in loaded_models():
            return False, (
                "Previous final model may still be resident "
                "(cannot verify /api/ps after unload)."
            )
        return True, f"unloaded managed models: {', '.join(to_unload)}"
    for name in to_unload:
        if any(name == r or name in r for r in resident_final):
            return False, f"Previous final model is still resident: {name}"

    return True, f"unloaded managed models: {', '.join(to_unload)}"


def generate_final_summary(
    storage: SessionStorage,
    status: Optional[Callable[[str], None]] = None,
) -> str:
    def _st(msg: str) -> None:
        log.info(msg)
        if status:
            try:
                status(msg)
            except Exception:
                pass

    # Hard gate: refuse to load FINAL_MODEL if realtime models still resident
    allowed, reason = can_load_final_model()
    if not allowed:
        raise RuntimeError(f"cleanup gate failed: {reason}")

    _st("释放实时视觉模型…")
    release_ok, release_errs = release_realtime_models(exclude={FINAL_MODEL})
    if not release_ok:
        # cleanup failed — refuse to load 27B (hard constraint)
        raise RuntimeError(
            f"CLEANUP_FAILED: cannot load {FINAL_MODEL}: {'; '.join(release_errs)}"
        )
    if release_errs:
        log.warning("model release warnings: %s", "; ".join(release_errs))
        _st(f"模型释放警告: {'; '.join(release_errs)}")
    time.sleep(1.0)

    # Double-check gate after release
    allowed, reason = can_load_final_model()
    if not allowed:
        raise RuntimeError(f"cleanup gate failed after release: {reason}")

    transcript_md = storage.read_text_safe(storage.transcript_path)
    if not transcript_md:
        # fallback: any transcript_*.md left unrename
        for p in storage.dir.glob("transcript_*.md"):
            transcript_md = p.read_text(encoding="utf-8")
            break
    visual_lines = storage.read_visual_lines()
    state_lines = storage.read_state_lines()
    live_notes = storage.read_text_safe(storage.live_notes_path)

    if not transcript_md and not visual_lines and not state_lines:
        storage.final_summary_path.write_text(
            "# 课程总结\n\n（本会话没有可用的转写或视觉数据，无法生成总结。）\n",
            encoding="utf-8",
        )
        return str(storage.final_summary_path)

    # give the model live_notes too (prompt template mentions it)
    prompt = build_final_summary_prompt(transcript_md, visual_lines, state_lines)
    prompt += "\n\n== live_notes.md ==\n" + (live_notes or "（无）")

    from core.ollama_client import chat_text  # reuse QLens client

    _st(f"加载 {FINAL_MODEL} 生成最终总结…")
    summary = chat_text(
        FINAL_SUMMARY_SYSTEM,
        prompt,
        temperature=0.3,
        timeout=max(REQUEST_TIMEOUT, 600),  # 27B cold load + long generation
        model=FINAL_MODEL,
        keep_alive="10m",
        num_predict=8192,
        num_ctx=NUM_CTX_FINAL,
        think=False,  # verified on qwen38-27b: disables thinking, ~1.5x faster
    )
    summary = (summary or "").strip() or "（模型返回为空）"
    mark_model_loaded(FINAL_MODEL)

    header = f"<!-- generated {time.strftime('%Y-%m-%d %H:%M:%S')} by {FINAL_MODEL} -->\n\n"
    storage.final_summary_path.write_text(header + summary + "\n", encoding="utf-8")
    _st(f"final_summary.md 已写入: {storage.final_summary_path}")
    return str(storage.final_summary_path)
