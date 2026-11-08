"""Blind Vision Frame Describer (`describe_frames`) for unbiased UI, layout, animation, and 3D shader inspection.

Replicates and generalizes `ramenvr/autopilot/autopilot/vision.py`:
- The vision describer is NEVER told the user's request or what the coding agent hopes to see.
- It receives only the captured frames (`Frames`) and 1–4 open, non-leading questions (`validate_open_questions`),
  plus an automatic visual anomaly `SWEEP` checking for layout overflow, clipped text, overlapping elements,
  broken images/canvases, or generic AI-slop artifacts.
"""

from __future__ import annotations

import collections
import re
import time
from typing import Any

MAX_FRAMES = 12
DEFAULT_FRAMES = 6
MAX_QUESTIONS = 4
KEPT_BYTES = 64 * 1024 * 1024  # 64 MB ring buffer of recent frames

SYSTEM_PROMPT = """You describe screenshots/frames of a Web, Mobile, or 3D application for an engineer who cannot see them.
You are NOT told what they are testing, and you must never guess: report only what is visibly present.

- Answer each numbered question in order, then answer the final visual sweep question.
- Layout & Typography: describe alignment, spacing, font hierarchy, contrast, and whether any element overflows its container.
- Interactive Controls & States: state whether a skeleton/spinner, empty-state message, error banner, or data view is shown.
- Text & Numbers: quote visible text and numbers verbatim where readable; if clipped or unreadable, say "unreadable".
- Motion / 3D Canvas (across multiple frames): state what moved, rotated, faded, or changed between frame numbers.
- Never judge whether something is "correct" or "working" - describe what is on screen in concrete visual terms.
- At most 400 words."""

SWEEP_PROMPT = (
    "Last: anything in these frames that looks visually broken or anomalous for a production app "
    "(text cut off or overlapping, horizontal overflow, unstyled default browser controls, broken image icons, "
    "blank/black canvas, misaligned cards, or elements covering interactive buttons): state what and in which frames; "
    "or state that no visual anomalies show."
)

LEADING_RE = re.compile(
    r"^(is|are|does|do|did|has|have|was|were|can|should|verify|confirm)\b",
    re.I,
)


def validate_open_questions(questions: list[str]) -> tuple[list[str], list[str]]:
    """Validate that `questions` are 1..MAX_QUESTIONS open questions (not yes/no or leading expectations).

    Returns `(cleaned_questions, warnings)`.
    """
    cleaned = [q.strip() for q in (questions or []) if isinstance(q, str) and q.strip()][:MAX_QUESTIONS]
    warnings: list[str] = []
    if not cleaned:
        raise ValueError("describe_frames requires 1 to 4 open questions.")
    for q in cleaned:
        if LEADING_RE.search(q):
            warnings.append(
                f"Question '{q}' looks like a yes/no or leading question; prefer open phrasing ('What is shown in...')."
            )
    return cleaned, warnings


class Frames:
    """Bounded in-memory ring buffer of captured episode frames (`(app_time, wall_time, bytes, label)`)."""

    def __init__(self, max_bytes: int = KEPT_BYTES) -> None:
        self.max_bytes = max_bytes
        self._items: collections.deque[dict[str, Any]] = collections.deque()
        self._bytes = 0

    def add(self, data: bytes, app_time: float = 0.0, label: str = "") -> None:
        if not data:
            return
        size = len(data)
        while self._items and self._bytes + size > self.max_bytes:
            old = self._items.popleft()
            self._bytes -= len(old["bytes"])
        self._items.append(
            {
                "t": round(float(app_time), 3),
                "wall": time.time(),
                "bytes": data,
                "label": label,
            }
        )
        self._bytes += size

    def select(
        self,
        start: float | None = None,
        end: float | None = None,
        count: int = DEFAULT_FRAMES,
    ) -> list[dict[str, Any]]:
        """Select up to `count` evenly spaced frames within `[start, end]`."""
        items = list(self._items)
        if start is not None:
            items = [f for f in items if f["t"] >= start]
        if end is not None:
            items = [f for f in items if f["t"] <= end]
        limit = max(1, min(int(count or DEFAULT_FRAMES), MAX_FRAMES))
        if len(items) <= limit:
            return items
        step = (len(items) - 1) / float(limit - 1)
        return [items[round(i * step)] for i in range(limit)]


def build_blind_vision_payload(
    questions: list[str],
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construct the strictly isolated prompt payload for a vision model or `frame-describer` subagent.

    Guarantees that the user's request and expected outcome are excluded from the payload.
    """
    cleaned, warnings = validate_open_questions(questions)
    numbered = "\n".join(f"{idx + 1}. {q}" for idx, q in enumerate(cleaned))
    user_prompt = f"{numbered}\n{len(cleaned) + 1}. {SWEEP_PROMPT}"
    return {
        "system": SYSTEM_PROMPT,
        "prompt": user_prompt,
        "questions": cleaned,
        "warnings": warnings,
        "frame_count": len(frames),
        "frame_times": [f.get("t", 0.0) for f in frames],
    }
