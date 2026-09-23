"""Session storage: JSONL events + live notes markdown (incremental)."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .settings import ACTIVE_SESSION_MARKER, SESSIONS_DIR


class SessionStorage:
    def __init__(self, session_dir: Path | None = None) -> None:
        if session_dir is not None:
            self.dir = Path(session_dir)
        else:
            # Second precision + monotonic suffix: two sessions started in the
            # same minute must never share a directory (STOP_REQUEST isolation).
            base = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.dir = SESSIONS_DIR / base
            n = 1
            while self.dir.exists():
                self.dir = SESSIONS_DIR / f"{base}_{n}"
                n += 1
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self.events_path = self.dir / "events.jsonl"
        self.visual_events_path = self.dir / "visual_events.jsonl"
        self.course_state_path = self.dir / "course_state.jsonl"
        self.live_notes_path = self.dir / "live_notes.md"
        self.transcript_path = self.dir / "transcript.md"          # final name
        self.final_summary_path = self.dir / "final_summary.md"
        self.partial_notes_path = self.dir / "partial_notes.md"
        # Round 12: one line per realtime fusion attempt (always on; no prompts).
        self.realtime_fusion_status_path = self.dir / "realtime_fusion_status.jsonl"
        self._transcript_writer_path: Path | None = None           # Hearsay-named file
        self._live_initialized = False
        self._active_marker_written = False

    # ---------- active session marker (sessions/current_session.json) ----------
    def write_active_marker(self) -> None:
        """Point stop_course.ps1 / external tools at THIS session dir."""
        payload = {
            "session_dir": self.dir.name,
            "session_path": str(self.dir),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "pid": None,
        }
        try:
            import os

            payload["pid"] = os.getpid()
        except Exception:
            pass
        try:
            ACTIVE_SESSION_MARKER.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._active_marker_written = True
        except Exception:
            pass

    def clear_active_marker(self) -> None:
        if not self._active_marker_written:
            return
        try:
            if ACTIVE_SESSION_MARKER.exists():
                data = json.loads(ACTIVE_SESSION_MARKER.read_text(encoding="utf-8"))
                # only clear if it still points at us
                if data.get("session_dir") == self.dir.name:
                    ACTIVE_SESSION_MARKER.unlink()
        except Exception:
            pass
        self._active_marker_written = False

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

    def append_realtime_fusion_status(self, obj: Any) -> None:
        """Append one realtime-fusion diagnostic record (Round 12)."""
        self._append_jsonl(self.realtime_fusion_status_path, obj)

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

    # ---------- partial notes (failure path; no full 27B summary) ----------
    def write_partial_notes(self, reason: str = "") -> Path:
        """Write partial_notes.md from whatever already exists (no model call)."""
        parts: list[str] = []
        header = (
            f"# 部分课程笔记（未生成完整课后总结）\n\n"
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        if reason:
            header += f"原因: {reason}\n"
        parts.append(header)

        live = self.read_text_safe(self.live_notes_path)
        if live.strip():
            parts.append("## 实时笔记 live_notes.md\n\n" + live)
        tr = self.read_text_safe(self.transcript_path)
        if not tr:
            for p in self.dir.glob("transcript_*.md"):
                tr = p.read_text(encoding="utf-8")
                break
        if tr.strip():
            parts.append("## 转写摘录\n\n" + tr[:20000])
        vis = self.read_visual_lines()
        if vis:
            parts.append("## 视觉事件\n\n" + "\n".join(vis[:50]))
        st = self.read_state_lines()
        if st:
            parts.append("## 增量状态\n\n" + "\n".join(st[:50]))

        text = "\n\n".join(parts) + "\n"
        with self._lock:
            self.partial_notes_path.write_text(text, encoding="utf-8")
        return self.partial_notes_path

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
