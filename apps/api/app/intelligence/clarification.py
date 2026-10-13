"""A work package pauses when a required fact is missing and only Lee can supply it."""

from __future__ import annotations

import re

from .think import strip_think

_ASK = re.compile(r"(?im)^\s*(?:NEED|ASK LEE|CLARIFICATION)\s*:\s*(.+?)\s*$")
_VAGUE = re.compile(r"(?i)^(research|find out|look up|check|draft)\s*(it|this|that)?\s*\.?!?$")
_OPENER = re.compile(
    r"(?i)^(please\s+)?(can you\s+)?(research|find out|look up|check|draft|write|create|make|prepare|help(?:\s+me)?)\b"
)
_SUBJECT = re.compile(r"(?i)\b(for|about|regarding|on|into|of)\s+\S+")


def blocking_question(objective: str) -> str:
    """Return the question Ayven cannot invent, or '' when work can proceed."""
    text = strip_think(objective or "").strip()
    match = _ASK.search(text)
    if match:
        question = strip_think(match.group(1)).strip().splitlines()[0].strip()
        return question[:400]
    if _VAGUE.match(text) or (_OPENER.match(text) and len(text) < 12):
        return "What exactly should this job cover? The request does not name a subject."
    if len(text) < 12:
        from .planner import classify

        if classify(text) in {"trivial", "calculation"}:
            return ""
        return "What exactly should this job cover? The request does not name a subject."
    if _OPENER.match(text) and not _SUBJECT.search(text) and not re.search(r"\d", text) and len(text) < 90:
        return "What should this cover? Name the subject and the deliverable."
    return ""
