"""Session storage: JSONL events + live notes markdown (incremental)."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .settings import SESSIONS_DIR


class SessionStorage:
    def __init__(self, session_dir: Path | None = None) -> None:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.dir = Path(session_dir) if session_dir else SESSIONS_DIR / ts
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self.events_path = self.dir / "events.jsonl"
        self.visual_events_path = self.dir / "visual_events.jsonl"
        self.course_state_path = self.dir / "course_state.jsonl"
        self.live_notes_path = self.dir / "live_notes.md"
        self.transcript_path = self.dir / "transcript.md"          # final name
        self.final_summary_path = self.dir / "final_summary.md"
        self._transcript_writer_path: Path | None = None           # Hearsay-named file
        self._live_initialized = False

    # ---------- jsonl ----------
    def _append_jsonl(self, path: Path, obj: Any) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def append_event(self, obj: Any) -> None:
        self._append_jsonl(self.events_path, obj)

    def append_visual_event(self, obj: Any) -> None:
        self._append_jsonl(self.visual_events_path, obj)

    def append_course_state(self, obj: Any) -> None:
        self._append_jsonl(self.course_state_path, obj)

    # ---------- live notes ----------
    def ensure_live_header(self, title: str) -> None:
        with self._lock:
            if self._live_initialized:
                return
            if not self.live_notes_path.exists():
                self.live_notes_path.write_text(
                    f"# {title}\n\n*课程进行中，实时更新…*\n",
                    encoding="utf-8",
                )
            self._live_initialized = True

    def append_live_section(self, header: str, body: str) -> None:
        with self._lock:
            with open(self.live_notes_path, "a", encoding="utf-8") as f:
                f.write(f"\n{header}\n\n{body}\n")

    # ---------- transcript (Hearsay MarkdownWriter handoff) ----------
    def set_transcript_writer_path(self, path: Path) -> None:
        self._transcript_writer_path = Path(path)

    def finalize_transcript(self) -> Path | None:
        """Rename Hearsay's transcript_*.md to transcript.md (after writer.finalize)."""
        p = self._transcript_writer_path
        if p and p.exists() and p != self.transcript_path:
            try:
                if self.transcript_path.exists():
                    self.transcript_path.unlink()
                p.rename(self.transcript_path)
            except Exception:
                return p
        return self.transcript_path if self.transcript_path.exists() else p

    # ---------- readers (for final summary) ----------
    def read_text_safe(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def read_jsonl_objs(self, path: Path) -> list[dict]:
        out: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        out.append(obj)
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def read_visual_lines(self) -> list[str]:
        objs = self.read_jsonl_objs(self.visual_events_path)
        lines = []
        for o in objs:
            t = o.get("session_t", o.get("timestamp", ""))
            desc = o.get("description", "")
            if isinstance(desc, dict):
                desc = json.dumps(desc, ensure_ascii=False)
            lines.append(f"[t={t}] {desc}")
        return lines

    def read_state_lines(self) -> list[str]:
        objs = self.read_jsonl_objs(self.course_state_path)
        return [json.dumps(o, ensure_ascii=False) for o in objs]


def fmt_t(session_t: float) -> str:
    """mm:ss from session-relative seconds."""
    s = int(max(0, session_t))
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"
