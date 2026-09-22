"""Unified event types for the course session."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


def _now() -> float:
    return time.time()


@dataclass
class TranscriptEvent:
    timestamp: float = field(default_factory=_now)  # wall-clock seconds
    duration: float = 0.0
    text: str = ""
    source: str = "system_audio"
    # session-relative seconds since recording start (for display)
    session_t: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VisualEvent:
    timestamp: float = field(default_factory=_now)
    frame_id: int = 0
    description: str = ""
    changed: bool = True
    source: str = "screen"
    session_t: float = 0.0
    diff_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CourseState:
    current_topic: str = ""
    current_concepts: List[str] = field(default_factory=list)
    current_visual_content: str = ""
    formulas: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    teacher_emphasis: List[str] = field(default_factory=list)
    teacher_explanation: List[str] = field(default_factory=list)
    pitfalls: List[str] = field(default_factory=list)
    relations: List[str] = field(default_factory=list)
    unresolved_points: List[str] = field(default_factory=list)
    recent_transcript: str = ""
    recent_visual_events: str = ""
    updates: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CourseState":
        known = {f: d.get(f) for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)
