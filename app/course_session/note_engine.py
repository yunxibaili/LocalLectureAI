"""Incremental course note engine: recent events + CourseState -> delta update.

Round 5: fuse_final() runs post-class fusion on FINAL_MODEL without mutating
the realtime model role. fuse() skip=True is a pure no-op (no registry mark).

Round 13: realtime fuse() uses chat_fusion (Ollama format=json + thinking
harvest) so qwen3-vl answers land in parseable JSON instead of empty content.
fuse_final() still uses QLens chat_text on FINAL_MODEL (unchanged).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional, Tuple

from .events import CourseState, TranscriptEvent, VisualEvent
from .prompts import FUSION_SYSTEM, build_fusion_prompt
from .settings import (
    FINAL_MODEL,
    NUM_CTX_FINAL,
    NUM_CTX_LIVE,
    OLLAMA_URL,
    REALTIME_FUSION_MODEL,
    REALTIME_FUSION_NUM_PREDICT,
    RECENT_TRANSCRIPT_EVENTS,
    RECENT_VISUAL_EVENTS,
    REQUEST_TIMEOUT,
    mark_model_loaded,
)
from .storage import SessionStorage, fmt_t

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
# Invalid JSON string escapes (e.g. LaTeX "\\c" from models that omit "\\").
_INVALID_JSON_ESCAPE_RE = re.compile(r'\\(?![\\"bfnrtu/])')

# Explicit outcomes for final fusion (never bool-only ambiguity).
FUSION_SUCCESS = "success"
FUSION_NOOP = "no-op"
FUSION_FAILURE = "failure"

# Realtime fuse() diagnostic outcomes (Round 12): stored on NoteEngine.last_fuse.
# Behavior of fuse() return value is unchanged (True only on applied delta).
FUSE_OK = "success"
FUSE_SKIP = "skip"
FUSE_INVALID_JSON = "invalid_json"
FUSE_TIMEOUT = "timeout"
FUSE_EXCEPTION = "exception"
FUSE_EMPTY = "empty"
FUSE_FAILURE = "failure"


def parse_fusion_json(raw: str) -> Tuple[Optional[dict], str]:
    """Parse one fusion model payload. Returns (dict|None, parse_stage).

    Stages: empty | direct | fence | brace | escape_repaired | brace_escape_repaired | invalid.
    Never logs or returns the full raw body.
    """
    if not raw or not str(raw).strip():
        return None, "empty"
    s = str(raw).strip()
    candidates: List[Tuple[str, str]] = []
    stripped = _FENCE_RE.sub("", s).strip()
    candidates.append((stripped, "direct" if stripped == s else "fence"))
    candidates.append((_INVALID_JSON_ESCAPE_RE.sub(r"\\\\", stripped), "escape_repaired"))
    m = re.search(r"\{[\s\S]*\}", stripped)
    if m:
        candidates.append((m.group(0), "brace"))
        candidates.append((_INVALID_JSON_ESCAPE_RE.sub(r"\\\\", m.group(0)), "brace_escape_repaired"))
    for text, stage in candidates:
        if not text:
            continue
        try:
            obj = json.loads(text)
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj, stage
    return None, "invalid"


def _parse_json_loose(raw: str) -> Optional[dict]:
    obj, _stage = parse_fusion_json(raw)
    return obj


def chat_fusion(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    temperature: float = 0.2,
    num_ctx: int = NUM_CTX_LIVE,
    num_predict: Optional[int] = None,
    timeout: Optional[int] = None,
    think: Optional[bool] = False,
    diag: Optional[dict] = None,
) -> str:
    """Realtime fusion chat: Ollama format=json + content/thinking harvest.

    Round 13 root cause: qwen3-vl:8b often returns the JSON only in
    message.thinking (content empty) when format=json is used. chat_text()
    only reads content → empty → invalid_json/failure. This helper posts
    directly (does not modify QLens) and returns content or thinking text.
    HTTP status and response_chars are recorded on diag when provided.
    Full prompt/response are never stored.
    """
    import requests

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": temperature,
            "num_predict": int(num_predict or REALTIME_FUSION_NUM_PREDICT),
            "num_ctx": int(num_ctx),
        },
    }
    if think is not None:
        payload["think"] = think
    t0 = time.perf_counter()
    try:
        r = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=timeout if timeout is not None else REQUEST_TIMEOUT,
        )
    except Exception:
        if diag is not None:
            diag["http_status"] = None
            diag["response_chars"] = 0
            diag["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        raise
    if diag is not None:
        diag["http_status"] = r.status_code
        diag["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()
    # Prefer parseable content; fall back to thinking (qwen3-vl often uses it).
    text = content or thinking
    if diag is not None:
        diag["response_chars"] = len(text)
        diag["response_source"] = "content" if content else ("thinking" if thinking else "none")
    return text


def _extend_unique(target: List[str], items) -> None:
    if not items:
        return
    if isinstance(items, str):
        items = [items]
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        if s not in target:
            target.append(s)


class NoteEngine:
    """Applies one fusion delta to the CourseState and renders a live section."""

    def __init__(self, storage: SessionStorage, model: str = REALTIME_FUSION_MODEL) -> None:
        self.storage = storage
        self.model = model
        self.state = CourseState()
        # Round 12: structured diagnostics for the last realtime fuse() call.
        # Never contains full prompts or full model responses.
        self.last_fuse: dict = {
            "result": None,
            "input_chars": 0,
            "prompt_chars": 0,
            "transcript_chars": 0,
            "visual_chars": 0,
            "response_chars": 0,
            "parse_stage": None,
            "model": None,
            "http_status": None,
            "exception_type": None,
            "error_message": None,
            "latency_ms": 0,
        }
        self._chat_exc_type: str | None = None
        self._chat_exc_msg: str | None = None
        self._chat_timeout: bool = False
        self._chat_http_status: int | None = None
        self._chat_response_chars: int = 0
        self._chat_response_source: str | None = None
        self._parse_stage: str | None = None
        self._prompt_chars: int = 0
        self._last_raw: str = ""

    # ------------------------------------------------------------------
    def fuse(
        self,
        transcripts: List[TranscriptEvent],
        visuals: List[VisualEvent],
    ) -> bool:
        """Run one realtime fusion step. Returns True only if state was updated.

        Registry rules (Round 5):
        - skip=True / invalid JSON / exception → False, NO mark_model_loaded.
        - Applied delta → True + mark_model_loaded(self.model).
        Callers must not register the model when this returns False.

        Round 12: always refreshes self.last_fuse with a structured outcome
        (success|skip|invalid_json|timeout|exception|empty|failure). Does not
        change the boolean contract or consume pending (caller owns pending).

        Round 13: realtime path uses chat_fusion (format=json + thinking
        harvest), not QLens chat_text. fuse_final() still uses chat_text.
        """
        diag: dict = {
            "result": FUSE_FAILURE,
            "input_chars": 0,
            "prompt_chars": 0,
            "transcript_chars": 0,
            "visual_chars": 0,
            "response_chars": 0,
            "parse_stage": None,
            "model": self.model,
            "http_status": None,
            "exception_type": None,
            "error_message": None,
            "latency_ms": 0,
        }
        t0 = time.perf_counter()
        try:
            tr_lines = self._transcript_lines(transcripts)
            vi_lines = self._visual_lines(visuals)
            diag["transcript_chars"] = sum(len(x) for x in tr_lines)
            diag["visual_chars"] = sum(len(x) for x in vi_lines)
            diag["input_chars"] = diag["transcript_chars"] + diag["visual_chars"]
            if not tr_lines and not vi_lines:
                diag["result"] = FUSE_EMPTY
                diag["parse_stage"] = "empty_input"
                return False

            self._chat_exc_type = None
            self._chat_exc_msg = None
            self._chat_timeout = False
            self._chat_http_status = None
            self._chat_response_chars = 0
            self._chat_response_source = None
            self._parse_stage = None
            self._prompt_chars = 0
            self._last_raw = ""
            raw = self._chat_fusion(
                tr_lines,
                vi_lines,
                model=self.model,
                num_ctx=NUM_CTX_LIVE,
                think=False,
                timeout=REQUEST_TIMEOUT,
            )
            diag["prompt_chars"] = self._prompt_chars
            diag["http_status"] = self._chat_http_status
            diag["response_chars"] = self._chat_response_chars
            if raw is None:
                if self._chat_timeout:
                    diag["result"] = FUSE_TIMEOUT
                    diag["parse_stage"] = "timeout"
                    diag["exception_type"] = self._chat_exc_type or "Timeout"
                    diag["error_message"] = self._chat_exc_msg
                elif self._chat_exc_type:
                    diag["result"] = FUSE_EXCEPTION
                    diag["parse_stage"] = "exception"
                    diag["exception_type"] = self._chat_exc_type
                    diag["error_message"] = self._chat_exc_msg
                else:
                    diag["result"] = FUSE_FAILURE
                    diag["parse_stage"] = "empty_response"
                    diag["error_message"] = "empty chat response"
                return False

            delta, stage = parse_fusion_json(raw)
            self._parse_stage = stage
            diag["parse_stage"] = stage
            if delta is None:
                log.warning("fusion response not JSON (ignored): stage=%s len=%d",
                            stage, len(raw))
                diag["result"] = FUSE_INVALID_JSON
                diag["error_message"] = f"response not JSON (stage={stage})"
                return False
            if delta.get("skip") is True:
                # no-op: model may have loaded, but skip must NOT register as success
                log.info("fusion skip (no valuable new info)")
                diag["result"] = FUSE_SKIP
                return False

            mark_model_loaded(self.model)
            self._apply_delta(delta)
            self._persist_section(tr_lines, vi_lines)
            diag["result"] = FUSE_OK
            return True
        except Exception as e:
            # Round 12: fuse itself must not raise for ordinary chat failures;
            # still classify so session can write realtime_fusion_status.
            log.error("fuse raised: %s: %s", type(e).__name__, e)
            diag["result"] = FUSE_EXCEPTION
            diag["exception_type"] = type(e).__name__
            diag["error_message"] = str(e)[:300]
            if diag.get("parse_stage") is None:
                diag["parse_stage"] = "exception"
            return False
        finally:
            diag["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            # Shallow copy so callers cannot mutate the stored snapshot by accident.
            self.last_fuse = dict(diag)

    def fuse_final(
        self,
        transcripts: List[TranscriptEvent],
        visuals: List[VisualEvent],
        model: Optional[str] = None,
    ) -> str:
        """Post-class final fusion on FINAL_MODEL (explicit model, no global swap).

        Returns FUSION_SUCCESS | FUSION_NOOP | FUSION_FAILURE.
        - No pending events → FUSION_NOOP (does not call the model).
        - skip=True or empty applied work → FUSION_NOOP (no registry mark).
        - Applied delta → FUSION_SUCCESS + mark_model_loaded(model).
        - Model/error → FUSION_FAILURE (no registry mark).
        Does not mutate self.model (realtime role stays REALTIME_FUSION_MODEL).
        """
        target = model or FINAL_MODEL
        if not transcripts and not visuals:
            return FUSION_NOOP

        tr_lines = self._transcript_lines(transcripts)
        vi_lines = self._visual_lines(visuals)
        if not tr_lines and not vi_lines:
            return FUSION_NOOP

        raw = self._chat_fusion_final(
            tr_lines,
            vi_lines,
            model=target,
            num_ctx=NUM_CTX_FINAL,
            think=False,
            timeout=max(REQUEST_TIMEOUT, 300),
        )
        if raw is None:
            return FUSION_FAILURE

        delta = _parse_json_loose(raw)
        if delta is None:
            log.warning("final fusion response not JSON: %s", (raw or "")[:200])
            return FUSION_FAILURE
        if delta.get("skip") is True:
            log.info("final fusion skip (no valuable new info)")
            return FUSION_NOOP

        mark_model_loaded(target)
        self._apply_delta(delta)
        self._persist_section(tr_lines, vi_lines)
        return FUSION_SUCCESS

    def _chat_fusion_final(
        self,
        tr_lines: List[str],
        vi_lines: List[str],
        *,
        model: str,
        num_ctx: int,
        think: Optional[bool] = None,
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """Post-class chat via QLens chat_text (FINAL path; Round 13 unchanged)."""
        from core.ollama_client import chat_text  # reuse QLens client

        user = build_fusion_prompt(self.state.to_dict(), tr_lines, vi_lines)
        raw = ""
        self._chat_exc_type = None
        self._chat_exc_msg = None
        self._chat_timeout = False
        try:
            for attempt in range(2):
                raw = chat_text(
                    FUSION_SYSTEM, user, temperature=0.2, model=model,
                    num_predict=6144, num_ctx=num_ctx,
                    think=think, timeout=timeout,
                )
                if raw and _parse_json_loose(raw) is not None:
                    return raw
                log.warning("final fusion attempt %d not JSON; retrying", attempt + 1)
                time.sleep(1.0)
            return raw or None
        except Exception as e:
            ename = type(e).__name__
            emsg = str(e)[:300]
            log.error("final fusion chat_text failed: %s: %s", ename, e)
            self._chat_exc_type = ename
            self._chat_exc_msg = emsg
            low = (ename + " " + emsg).lower()
            self._chat_timeout = (
                "timeout" in low or "timed out" in low or "readtimeout" in low
            )
            return None

    # ------------------------------------------------------------------
    def _transcript_lines(self, transcripts: List[TranscriptEvent]) -> List[str]:
        return [
            f"[{fmt_t(e.session_t)}] {e.text}"
            for e in transcripts[-RECENT_TRANSCRIPT_EVENTS:]
        ]

    def _visual_lines(self, visuals: List[VisualEvent]) -> List[str]:
        return [
            f"[{fmt_t(e.session_t)}] 画面变化(diff={e.diff_score}): "
            f"{e.description.replace(chr(10), ' ')}"
            for e in visuals[-RECENT_VISUAL_EVENTS:]
        ]

    def _chat_fusion(
        self,
        tr_lines: List[str],
        vi_lines: List[str],
        *,
        model: str,
        num_ctx: int,
        think: Optional[bool] = False,
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """One fusion chat with retry. Returns raw text or None on failure.

        Round 12: records exception type / timeout on self._chat_* for
        last_fuse diagnostics. Does not change return contract.
        Round 13: uses chat_fusion (format=json + thinking harvest). Tests
        patch app.course_session.note_engine.chat_fusion.
        """
        user = build_fusion_prompt(self.state.to_dict(), tr_lines, vi_lines)
        self._prompt_chars = len(FUSION_SYSTEM) + len(user)
        chat_diag: dict = {}
        raw = ""
        self._chat_exc_type = None
        self._chat_exc_msg = None
        self._chat_timeout = False
        self._chat_http_status = None
        self._chat_response_chars = 0
        self._chat_response_source = None
        try:
            for attempt in range(2):
                raw = chat_fusion(
                    FUSION_SYSTEM, user, model=model,
                    num_predict=REALTIME_FUSION_NUM_PREDICT,
                    num_ctx=num_ctx,
                    think=think, timeout=timeout,
                    diag=chat_diag,
                )
                self._chat_http_status = chat_diag.get("http_status")
                self._chat_response_chars = int(chat_diag.get("response_chars") or 0)
                self._chat_response_source = chat_diag.get("response_source")
                self._last_raw = raw or ""
                if raw and _parse_json_loose(raw) is not None:
                    return raw
                log.warning(
                    "fusion attempt %d not JSON (stage=%s); retrying",
                    attempt + 1,
                    parse_fusion_json(raw or "")[1],
                )
                time.sleep(1.0)
            return raw or None
        except Exception as e:
            ename = type(e).__name__
            emsg = str(e)[:300]
            log.error("fusion chat_fusion failed: %s: %s", ename, e)
            self._chat_exc_type = ename
            self._chat_exc_msg = emsg
            self._chat_http_status = chat_diag.get("http_status")
            low = (ename + " " + emsg).lower()
            self._chat_timeout = (
                "timeout" in low or "timed out" in low or "readtimeout" in low
            )
            return None

    def _persist_section(self, tr_lines: List[str], vi_lines: List[str]) -> None:
        self.state.recent_transcript = "\n".join(tr_lines[-10:])
        self.state.recent_visual_events = "\n".join(vi_lines[-5:])
        self.state.updates += 1
        self.storage.append_course_state(self.state.to_dict())
        header, body = self._render_section(tr_lines, vi_lines)
        self.storage.append_live_section(header, body)

    # ------------------------------------------------------------------
    def _apply_delta(self, d: dict) -> None:
        s = self.state
        topic = str(d.get("current_topic") or "").strip()
        if topic:
            s.current_topic = topic
        _extend_unique(s.current_concepts, d.get("new_knowledge"))
        _extend_unique(s.formulas, d.get("new_formulas"))
        _extend_unique(s.teacher_explanation, d.get("teacher_explanation"))
        _extend_unique(s.teacher_emphasis, d.get("teacher_emphasis"))
        _extend_unique(s.examples, d.get("examples"))
        _extend_unique(s.pitfalls, d.get("pitfalls"))
        _extend_unique(s.relations, d.get("relations"))
        _extend_unique(s.unresolved_points, d.get("unresolved"))
        vis = str(d.get("visual_note") or "").strip()
        if vis:
            s.current_visual_content = vis

    def _render_section(self, tr_lines: List[str], vi_lines: List[str]) -> tuple[str, str]:
        s = self.state
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        header = f"## {now} — {s.current_topic or '（主题未定）'}"

        parts: List[str] = []
        if tr_lines:
            parts.append("### 老师讲解（节选）\n" + "\n".join(f"- {l}" for l in tr_lines[-6:]))
        if s.current_concepts:
            parts.append("### 新知识点\n" + "\n".join(f"- {c}" for c in s.current_concepts[-8:]))
        if s.formulas:
            parts.append("### 公式\n" + "\n".join(f"$$\n{f}\n$$" if not f.strip().startswith("$$") else f
                                              for f in s.formulas[-8:]))
        if s.teacher_explanation:
            parts.append("### 老师解释\n" + "\n".join(f"- {c}" for c in s.teacher_explanation[-6:]))
        if s.teacher_emphasis:
            parts.append("### 老师强调\n" + "\n".join(f"- **{c}**" for c in s.teacher_emphasis[-6:]))
        if s.examples:
            parts.append("### 示例\n" + "\n".join(f"- {c}" for c in s.examples[-6:]))
        if s.pitfalls:
            parts.append("### 易错点\n" + "\n".join(f"- {c}" for c in s.pitfalls[-6:]))
        if vi_lines:
            parts.append("### 对应板书/画面\n" + "\n".join(f"- {l}" for l in vi_lines[-3:]))
        if s.unresolved_points:
            parts.append("### 待确认\n" + "\n".join(f"- {c}" for c in s.unresolved_points[-6:]))

        return header, "\n\n".join(parts) if parts else "（本段无新增内容）"


def fuse_final_status_safe(
    note_engine: NoteEngine,
    transcripts: List[TranscriptEvent],
    visuals: List[VisualEvent],
    model: str,
) -> str:
    """Wrap note_engine.fuse_final so run_final_phase never sees bare exceptions."""
    try:
        return note_engine.fuse_final(transcripts, visuals, model=model)
    except Exception as e:
        log.error("fuse_final raised: %s", e)
        return FUSION_FAILURE
