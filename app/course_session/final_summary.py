"""Post-class high-quality summary via qwen38-27b (exclusive GPU)."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .prompts import FINAL_SUMMARY_SYSTEM, build_final_summary_prompt
from .settings import FINAL_MODEL, NUM_CTX_FINAL, REQUEST_TIMEOUT
from .storage import SessionStorage

log = logging.getLogger(__name__)


def _release_vision_model() -> None:
    """Best-effort: evict the VLM so qwen38 can load exclusively."""
    import requests

    try:
        from .settings import OLLAMA_URL
        base = OLLAMA_URL.replace("/api/chat", "")
        requests.post(
            f"{base}/api/generate",
            json={"model": "qwen3-vl:8b", "keep_alive": 0},
            timeout=10,
        )
    except Exception:
        log.debug("vision model release skipped", exc_info=True)


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

    _st("释放实时视觉模型…")
    _release_vision_model()
    time.sleep(1.0)

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

    header = f"<!-- generated {time.strftime('%Y-%m-%d %H:%M:%S')} by {FINAL_MODEL} -->\n\n"
    storage.final_summary_path.write_text(header + summary + "\n", encoding="utf-8")
    _st(f"final_summary.md 已写入: {storage.final_summary_path}")
    return str(storage.final_summary_path)
