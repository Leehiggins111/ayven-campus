"""A work package pauses when a required fact is missing and only Lee can supply it."""

from __future__ import annotations

import re

from .think import strip_think

_ASK = re.compile(r"(?im)^\s*(?:NEED|ASK LEE|CLARIFICATION)\s*:\s*(.+?)\s*$")


"""A work package pauses when a required fact is missing and only Lee can supply it."""

from __future__ import annotations

import re

from .think import strip_think

_ASK = re.compile(r"(?im)^\s*(?:NEED|ASK LEE|CLARIFICATION)\s*:\s*(.+?)\s*$")
_VAGUE = re.compile(r"(?i)^(research|find out|look up|check|draft)\s*(it|this|that)?\s*\.?!?$")


def blocking_question(objective: str) -> str:
    """Return the question Ayven cannot invent, or '' when work can proceed."""
    text = strip_think(objective or "").strip()
    match = _ASK.search(text)
    if match:
        question = strip_think(match.group(1)).strip().splitlines()[0].strip()
        return question[:400]
    if len(text) < 12 or _VAGUE.match(text):
        return "What exactly should this job cover? The request does not name a subject."
    return ""

