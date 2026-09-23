"""Post-class high-quality summary via qwen38-27b (exclusive GPU).

Round 4: release_realtime_models and preflight_cleanup are driven by
/api/ps (authoritative), not by the local registry. Registry is only a
hint of what this process believes it loaded.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .prompts import FINAL_SUMMARY_SYSTEM, build_final_summary_prompt
from .settings import (
    FINAL_MODEL,
    NUM_CTX_FINAL,
    REALTIME_FUSION_MODEL,
    REQUEST_TIMEOUT,
    UNLOAD_RETRY_COUNT,
    UNLOAD_RETRY_DELAY_S,
    VISION_MODEL,
    CleanupStatus,
    forget_loaded_models,
    is_model_resident,
    list_loaded_models_via_api,
    loaded_models,
    mark_model_loaded,
    model_name_matches,
    ollama_base_url,
)
from .storage import SessionStorage

log = logging.getLogger(__name__)


def _managed_realtime_models() -> set[str]:
    """Models this project owns during the realtime phase (never 27B)."""
    return {m for m in (VISION_MODEL, REALTIME_FUSION_MODEL) if m}


def _release_named_models(names: set[str]) -> tuple[bool, list[str]]:
    """Unload exactly `names` based on /api/ps; never touch other models.

    Returns (success, error_messages). /api/ps None → FAIL.
    """
    import requests

    targets_in = sorted(n for n in names if n)
    if not targets_in:
        return True, []

    resident_before = list_loaded_models_via_api()
    if resident_before is None:
        return False, ["cannot verify /api/ps before release"]

    targets = sorted(
        m for m in targets_in if any(model_name_matches(m, r) for r in resident_before)
    )
    if not targets:
        return True, []

    errors: list[str] = []
    try:
        base = ollama_base_url()
    except Exception as e:
        return False, [f"base_url: {e}"]

    for name in targets:
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
                        errors.append(
                            f"{name}: cannot verify via /api/ps after unload "
                            f"(attempt {attempt+1})"
                        )
                        if attempt < UNLOAD_RETRY_COUNT - 1:
                            time.sleep(UNLOAD_RETRY_DELAY_S)
                            continue
            except Exception as e:
                errors.append(f"{name}: {e} (attempt {attempt+1})")
                if attempt < UNLOAD_RETRY_COUNT - 1:
                    time.sleep(UNLOAD_RETRY_DELAY_S)

        if not success:
            resident_final = is_model_resident(name)
            if resident_final is True:
                errors.append(
                    f"{name}: CONFIRMED STILL RESIDENT after {UNLOAD_RETRY_COUNT} attempts"
                )

    final_resident = list_loaded_models_via_api()
    if final_resident is None:
        return False, errors + ["cannot verify /api/ps — treating as cleanup failure"]
    still_resident = [
        name
        for name in targets
        if any(model_name_matches(name, r) for r in final_resident)
    ]
    if still_resident:
        return False, errors + [f"STILL RESIDENT: {', '.join(still_resident)}"]
    return True, errors


def release_realtime_models(exclude: set[str] | None = None) -> tuple[bool, list[str]]:
    """Release realtime models based on /api/ps (authoritative).

    Hard constraints:
    - Targets only VISION_MODEL / REALTIME_FUSION_MODEL (this app's roles).
    - Never touches other Ollama models (do not kill unrelated processes).
    - registry is NOT the source of truth: if /api/ps shows a managed
      model resident, unload it even when registry is empty.
    - unload -> /api/ps -> retry -> still resident after retries = FAIL.
    - /api/ps query failure (None) = FAIL (cannot verify).

    Returns (success, error_messages).
    """
    exclude = exclude or set()
    managed = _managed_realtime_models() - exclude
    return _release_named_models(managed)


def release_final_model() -> tuple[bool, list[str]]:
    """Unload ONLY FINAL_MODEL after final fusion/summary; verify via /api/ps.

    Never unloads unrelated Ollama models. /api/ps failure → FAIL.
    """
    if not FINAL_MODEL:
        return True, []
    return _release_named_models({FINAL_MODEL})


def is_final_model_resident() -> bool | None:
    """True/False if FINAL_MODEL is in /api/ps; None if cannot query."""
    resident = list_loaded_models_via_api()
    if resident is None:
        return None
    if not FINAL_MODEL:
        return False
    return any(model_name_matches(FINAL_MODEL, r) for r in resident)


def can_load_final_model() -> tuple[bool, str]:
    """Gate: may we load FINAL_MODEL (qwen38-27b)?

    Hard constraints:
    1. No worker threads still alive (caller must check this separately)
    2. /api/ps must show realtime models released
    3. cleanup must have succeeded

    Returns (allowed, reason).
    """
    resident = list_loaded_models_via_api()
    if resident is None:
        return False, "cannot verify /api/ps (query failed) — refuse to load 27B"

    realtime = _managed_realtime_models()
    # Also any registry models except FINAL_MODEL (this process may have
    # loaded something unexpected; still refuse if /api/ps shows it).
    realtime |= {m for m in (loaded_models() - {FINAL_MODEL}) if m}

    conflicts = []
    for rt in realtime:
        if any(model_name_matches(rt, r) for r in resident):
            conflicts.append(rt)
    if conflicts:
        return False, f"realtime models still resident: {', '.join(conflicts)}"

    return True, "ok"


def preflight_cleanup() -> tuple[bool, str]:
    """Called at CourseSession.start() BEFORE any workers start.

    Fail-closed: if /api/ps cannot be queried, refuse to start (cannot
    prove previous session's models are gone).

    Unloads leftover FINAL_MODEL / managed realtime models from a previous
    session. Only touches models THIS app declares as managed.

    Returns (ok, message). If not ok, session start must abort.
    """
    managed = {m for m in (FINAL_MODEL, VISION_MODEL, REALTIME_FUSION_MODEL) if m}

    resident = list_loaded_models_via_api()
    if resident is None:
        # UNKNOWN -> FAIL (do not use empty registry to override API failure)
        return False, "cannot verify /api/ps before realtime start — refusing to start (fail-closed)"

    to_unload = []
    for name in managed:
        if any(model_name_matches(name, r) for r in resident):
            to_unload.append(name)

    if not to_unload:
        return True, "no managed models resident"

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
                            f"Previous model is still resident: {name}. "
                            + "; ".join(errors)
                        )
                    time.sleep(UNLOAD_RETRY_DELAY_S)
                else:
                    return False, f"cannot verify /api/ps after unloading {name}"
            except Exception as e:
                errors.append(f"{name}: {e}")
                time.sleep(UNLOAD_RETRY_DELAY_S)

    resident_final = list_loaded_models_via_api()
    if resident_final is None:
        return False, "cannot verify /api/ps after preflight unload"
    for name in to_unload:
        if any(model_name_matches(name, r) for r in resident_final):
            return False, f"Previous model is still resident: {name}"

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


def run_final_phase(
    storage: SessionStorage,
    status: Optional[Callable[[str], None]] = None,
    *,
    pending_transcripts: Optional[list] = None,
    pending_visuals: Optional[list] = None,
    note_engine=None,
) -> tuple[Optional[str], Optional[str], list[str]]:
    """FINAL phase: Final Fusion → Final Summary → release FINAL_MODEL.

    Caller must already have released realtime models and passed
    can_load_final_model(). This function:
      1. fuse_final (no-op when no pending events — does not force 27B)
      2. generate_final_summary
      3. finally: release_final_model + /api/ps verify

    Returns (fusion_status, summary_path, errors).
    fusion_status: "success" | "no-op" | "failure" | None (summary-only path).
    """
    def _st(msg: str) -> None:
        log.info(msg)
        if status:
            try:
                status(msg)
            except Exception:
                pass

    errors: list[str] = []
    fusion_status: Optional[str] = None
    summary_path: Optional[str] = None
    pending_transcripts = pending_transcripts or []
    pending_visuals = pending_visuals or []

    try:
        # --- Final Fusion (explicit FINAL_MODEL; no-op if nothing pending) ---
        if note_engine is not None:
            if not pending_transcripts and not pending_visuals:
                fusion_status = "no-op"
                _st("No pending fusion work.")
            else:
                _st(f"Final Fusion（{FINAL_MODEL}）处理 pending events…")
                from .note_engine import fuse_final_status_safe

                fusion_status = fuse_final_status_safe(
                    note_engine, pending_transcripts, pending_visuals, FINAL_MODEL
                )
                if fusion_status == "failure":
                    errors.append("final fusion failed")
                    _st("Final Fusion 失败")
                elif fusion_status == "no-op":
                    _st("No pending fusion work.")
                else:
                    _st(f"Final Fusion 完成: {fusion_status}")

        # Round 6: FAILURE must NOT generate a full final_summary.md.
        # SUCCESS / NO-OP may proceed to summary. Cleanup still runs in finally.
        if fusion_status == "failure":
            errors.append("final summary skipped: final fusion failure")
            try:
                storage.write_partial_notes(
                    reason="Final Fusion failure — full final_summary.md skipped"
                )
                summary_path = str(storage.partial_notes_path)
                _st(f"partial_notes.md 已写入（因 Final Fusion 失败）: {summary_path}")
            except Exception as e:
                errors.append(f"partial notes write failed: {e}")
                log.error("partial notes after fusion failure", exc_info=True)
            return fusion_status, summary_path, errors

        # --- Final Summary (only after SUCCESS or NO-OP) ---
        allowed, reason = can_load_final_model()
        if not allowed:
            errors.append(f"ps gate before summary: {reason}")
            return fusion_status, None, errors

        summary_path = generate_final_summary(storage, status)
    finally:
        # --- FINAL_MODEL cleanup always (even on fusion/summary failure) ---
        try:
            rel_ok, rel_errs = release_final_model()
            if not rel_ok:
                errors.extend(rel_errs or ["final model release failed"])
                resident = is_final_model_resident()
                if resident is True:
                    errors.append(f"FINAL_MODEL still resident: {FINAL_MODEL}")
                elif resident is None:
                    errors.append("cannot verify /api/ps after final release")
            elif rel_errs:
                log.warning("final release warnings: %s", "; ".join(rel_errs))
        except Exception as e:
            errors.append(f"final release raised: {e}")
            log.error("release_final_model raised", exc_info=True)

    return fusion_status, summary_path, errors
