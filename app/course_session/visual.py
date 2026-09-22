"""Visual stream: continuous in-memory screen-region frames -> VLM events.

Reuses QLens capture/coords/ollama_client wholesale; the loop structure is
modeled on QLens VideoAnalyzer (grab -> prepare -> chat -> wait -> repeat),
with a lightweight frame-difference gate inserted BEFORE the VLM call so a
static PPT does not trigger repeated inference.

No screenshot files are written: frames stay in memory (jpeg b64 is built
only for the Ollama request body).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from typing import Any, Callable, Optional

import cv2
import numpy as np

from .settings import (
    FALLBACK_VISION_MODEL,
    FRAME_DIFF_HIGH,
    FRAME_DIFF_LOW,
    FRAME_POLL_S,
    FORCE_REFRESH_S,
    INFER_SIZE,
    MIN_VLM_INTERVAL_S,
    NUM_CTX_LIVE,
    VISION_MODEL,
    mark_model_loaded,
)

log = logging.getLogger(__name__)

# Import unmodified QLens modules (settings.py already put qlens on sys.path).
from core.capture import Region, grab                # noqa: E402 (upstream reuse)
from core.coords import prepare_image                # noqa: E402
from core.ollama_client import chat                  # noqa: E402

from .prompts import COURSE_VISUAL_SYSTEM, build_visual_prompt  # noqa: E402
from .events import VisualEvent                        # noqa: E402


def _diff_score(prev_gray: np.ndarray, cur_gray: np.ndarray) -> float:
    """Mean abs difference (0-255) between two grayscale frames (same size)."""
    if prev_gray.shape != cur_gray.shape:
        return 255.0
    diff = cv2.absdiff(prev_gray, cur_gray)
    return float(cv2.mean(diff)[0])


def _parse_json_loose(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(s[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def validate_visual_payload(obj: Any) -> tuple[bool, str]:
    """Schema check for COURSE_VISUAL_SYSTEM JSON. Returns (ok, reason)."""
    if not isinstance(obj, dict):
        return False, f"not a JSON object (got {type(obj).__name__})"
    # At least one primary content field must be a non-empty string.
    primary = ("one_line_summary", "page_text", "change_vs_prev")
    has_primary = any(
        isinstance(obj.get(k), str) and obj.get(k).strip() for k in primary
    )
    if not has_primary:
        return False, "missing primary content field (one_line_summary/page_text/change_vs_prev)"
    # Optional typed fields, when present, must have the right type.
    for key in ("new_formulas", "definitions", "examples"):
        if key in obj and obj[key] is not None and not isinstance(obj[key], list):
            return False, f"{key} must be array"
    if "has_example" in obj and not isinstance(obj["has_example"], bool):
        return False, "has_example must be boolean"
    for key in ("code", "chart", "confidence_note"):
        if key in obj and obj[key] is not None and not isinstance(obj[key], str):
            return False, f"{key} must be string"
    return True, ""


class VisualStream(threading.Thread):
    """Grab region frames continuously; emit VisualEvent only on real change."""

    def __init__(
        self,
        region: Region,
        on_event: Callable[[VisualEvent], None],
        model: str = VISION_MODEL,
        fallback_model: str = FALLBACK_VISION_MODEL,
        t0: Optional[float] = None,
    ) -> None:
        super().__init__(name="VisualStream", daemon=True)
        self._region = region
        self._on_event = on_event
        self._model = model
        self._fallback_model = fallback_model
        self._stop_evt = threading.Event()
        self._t0 = t0 if t0 is not None else time.monotonic()
        self.frame_id = 0
        self.vlm_calls = 0
        self.skipped_frames = 0
        self.failed_analyses = 0
        self.last_error: str = ""
        self.last_description: str = ""   # one-line/JSON summary for prev-state
        self.last_status: str = ""

    def stop(self) -> None:
        self._stop_evt.set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        prev_analyzed_gray: Optional[np.ndarray] = None
        prev_summary_for_prompt = ""
        last_vlm_ts = 0.0
        force_due_ts: Optional[float] = None  # low-change but long overdue

        self.last_status = "Visual stream started"
        while not self._stop_evt.is_set():
            loop_start = time.monotonic()
            try:
                frame = grab(self._region)
            except Exception as e:
                self.last_error = f"grab failed: {e}"
                log.error("grab failed\n%s", traceback.format_exc())
                self._stop_evt.wait(2.0)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # cheap downscale for diff stability/speed
            gray_small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)

            score = 255.0
            if prev_analyzed_gray is not None:
                score = _diff_score(prev_analyzed_gray, gray_small)

            now = time.monotonic()
            since_vlm = now - last_vlm_ts if last_vlm_ts else 1e9

            should_analyze = False
            reason = ""
            if prev_analyzed_gray is None:
                should_analyze, reason = True, "first frame"
            elif score >= FRAME_DIFF_HIGH and since_vlm >= MIN_VLM_INTERVAL_S:
                should_analyze, reason = True, f"high change ({score:.1f})"
            elif score >= FRAME_DIFF_LOW:
                # gradual change (handwriting): allow after cooldown
                if since_vlm >= MIN_VLM_INTERVAL_S:
                    should_analyze, reason = True, f"moderate change ({score:.1f})"
            elif score >= FRAME_DIFF_LOW * 0.5:
                # tiny change: force periodic refresh so long sessions stay fresh
                if force_due_ts is None:
                    force_due_ts = now + FORCE_REFRESH_S
                if now >= force_due_ts:
                    should_analyze, reason = True, f"force refresh ({score:.1f})"
            else:
                force_due_ts = None

            if not should_analyze:
                self.skipped_frames += 1
                self.last_status = (
                    f"frame unchanged (diff={score:.2f}), skip VLM "
                    f"(skipped={self.skipped_frames})"
                )
            else:
                desc, ok = self._analyze(frame, prev_summary_for_prompt)
                if ok:
                    prev_analyzed_gray = gray_small
                    last_vlm_ts = time.monotonic()
                    force_due_ts = None
                    self.vlm_calls += 1
                    prev_summary_for_prompt = self.last_description
                    ev = VisualEvent(
                        timestamp=time.time(),
                        frame_id=self.frame_id,
                        description=desc if isinstance(desc, str) else str(desc),
                        changed=score >= FRAME_DIFF_LOW or self.frame_id == 0,
                        session_t=time.monotonic() - self._t0,
                        diff_score=round(score, 2),
                    )
                    self.last_status = (
                        f"frame {self.frame_id} analyzed ({reason}), "
                        f"vlm_calls={self.vlm_calls}"
                    )
                    try:
                        self._on_event(ev)
                    except Exception:
                        log.error("on_event failed\n%s", traceback.format_exc())
                else:
                    self.failed_analyses += 1
                    # keep prev_analyzed unchanged so we retry next loop;
                    # do NOT count as success, do NOT advance last_vlm_ts
                    self._stop_evt.wait(3.0)

            self.frame_id += 1
            elapsed = time.monotonic() - loop_start
            remain = FRAME_POLL_S - elapsed
            if remain > 0:
                self._stop_evt.wait(remain)

        self.last_status = "Visual stream stopped"

    # ------------------------------------------------------------------
    def _chat_once(
        self,
        infer_img: np.ndarray,
        user_prompt: str,
        model: str,
        as_json: bool,
    ) -> str:
        raw = chat(
            infer_img,
            COURSE_VISUAL_SYSTEM,
            user_prompt,
            temperature=0.1,
            as_json=as_json,
            model=model,
            # Do NOT pass think=False: known Ollama bug (qwen family) breaks
            # format/empty-content. Thinking still runs; give it room.
            num_predict=NUM_CTX_LIVE,
            num_ctx=NUM_CTX_LIVE,
        )
        mark_model_loaded(model)
        return (raw or "").strip()

    def _analyze(self, frame: np.ndarray, prev_summary: str) -> tuple[str, bool]:
        """Call VLM on an in-memory frame. Returns (description, ok).

        ok=True only when the payload parses as JSON and passes schema
        validation. On validation failure: one as_json=False retry, then fail
        (no success, no state advance).
        """
        infer_img, _mapper = prepare_image(frame, self._region, INFER_SIZE)
        user_prompt = build_visual_prompt(prev_summary)

        raw = ""
        try:
            raw = self._chat_once(infer_img, user_prompt, self._model, as_json=True)
        except Exception as e:
            log.warning(
                "VLM %s failed (%s); trying fallback %s",
                self._model, e, self._fallback_model,
            )
            try:
                raw = self._chat_once(
                    infer_img, user_prompt, self._fallback_model, as_json=True
                )
            except Exception as e2:
                log.error("fallback VLM failed\n%s", traceback.format_exc())
                self.last_error = str(e2)
                return "", False

        obj = _parse_json_loose(raw) if raw else None
        if obj is not None:
            ok, reason = validate_visual_payload(obj)
            if ok:
                desc = raw
                one_line = str(obj.get("one_line_summary") or raw).replace("\n", " ")
                if len(one_line) > 500:
                    one_line = one_line[:500] + "…"
                self.last_description = one_line
                self.last_error = ""
                return desc, True
            log.warning("visual JSON invalid (%s); retrying as_json=False", reason)
        elif not raw:
            log.warning("empty visual content; retrying as_json=False")
        else:
            log.warning("visual payload not JSON; retrying as_json=False")

        # one retry without grammar (prompt still mandates JSON output)
        try:
            raw2 = self._chat_once(infer_img, user_prompt, self._model, as_json=False)
        except Exception:
            log.warning("as_json=False retry failed", exc_info=True)
            self.last_error = "visual JSON invalid after retry"
            return "", False
        obj2 = _parse_json_loose(raw2) if raw2 else None
        if obj2 is None:
            self.last_error = "visual response not JSON after retry"
            log.error("visual retry still not JSON: %s", (raw2 or "")[:200])
            return "", False
        ok2, reason2 = validate_visual_payload(obj2)
        if not ok2:
            self.last_error = f"visual schema invalid: {reason2}"
            log.error("visual retry schema fail: %s", reason2)
            return "", False

        one_line = str(obj2.get("one_line_summary") or raw2).replace("\n", " ")
        if len(one_line) > 500:
            one_line = one_line[:500] + "…"
        self.last_description = one_line
        self.last_error = ""
        return raw2, True
