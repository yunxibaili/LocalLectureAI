"""Incremental course note engine: recent events + CourseState -> delta update."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

from .events import CourseState, TranscriptEvent, VisualEvent
from .prompts import FUSION_SYSTEM, build_fusion_prompt
from .settings import (
    REALTIME_FUSION_MODEL,
    NUM_CTX_LIVE,
    RECENT_TRANSCRIPT_EVENTS,
    RECENT_VISUAL_EVENTS,
    mark_model_loaded,
)
from .storage import SessionStorage, fmt_t

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json_loose(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # best-effort: first {...} block
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


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

    # ------------------------------------------------------------------
    def fuse(
        self,
        transcripts: List[TranscriptEvent],
        visuals: List[VisualEvent],
    ) -> bool:
        """Run one fusion step. Returns True if state was updated.

        mark_model_loaded is ONLY called after a successful chat_text call
        that returned parseable content (including skip=True responses).
        Not called on exception or invalid/empty response.
        """
        from core.ollama_client import chat_text  # reuse QLens client

        tr_lines = [
            f"[{fmt_t(e.session_t)}] {e.text}"
            for e in transcripts[-RECENT_TRANSCRIPT_EVENTS:]
        ]
        vi_lines = [
            f"[{fmt_t(e.session_t)}] 画面变化(diff={e.diff_score}): "
            f"{e.description.replace(chr(10), ' ')}"
            for e in visuals[-RECENT_VISUAL_EVENTS:]
        ]
        if not tr_lines and not vi_lines:
            return False

        user = build_fusion_prompt(self.state.to_dict(), tr_lines, vi_lines)
        raw = ""
        model_loaded_ok = False
        try:
            for attempt in range(2):
                raw = chat_text(
                    FUSION_SYSTEM, user, temperature=0.2, model=self.model,
                    num_predict=6144, num_ctx=NUM_CTX_LIVE,
                )
                if raw:
                    # mark only after successful call (chat_text returned content)
                    model_loaded_ok = True
                if _parse_json_loose(raw) is not None:
                    break
                log.warning("fusion attempt %d not JSON; retrying", attempt + 1)
                time.sleep(1.0)
        except Exception as e:
            log.error("fusion chat_text failed: %s", e)
            return False

        delta = _parse_json_loose(raw)
        if delta is None:
            log.warning("fusion response not JSON (ignored): %s", (raw or "")[:200])
            # Do NOT mark_model_loaded on invalid response — model may not have loaded
            return False
        if delta.get("skip") is True:
            log.info("fusion skip (no valuable new info)")
            # skip is a valid response — model did load
            if model_loaded_ok:
                mark_model_loaded(self.model)
            return False

        # Valid delta — model loaded successfully
        if model_loaded_ok:
            mark_model_loaded(self.model)

        self._apply_delta(delta)
        self.state.recent_transcript = "\n".join(tr_lines[-10:])
        self.state.recent_visual_events = "\n".join(vi_lines[-5:])
        self.state.updates += 1

        # persist structured state
        self.storage.append_course_state(self.state.to_dict())
        # persist human-readable section
        header, body = self._render_section(tr_lines, vi_lines)
        self.storage.append_live_section(header, body)
        return True

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
