"""A work package pauses when a required fact is missing and only Lee can supply it."""

from __future__ import annotations

import re

from .think import strip_think

_ASK = re.compile(r"(?im)^\s*(?:NEED|ASK LEE|CLARIFICATION)\s*:\s*(.+?)\s*$")


def blocking_question(objective: str) -> str:
    """Return the question Ayven cannot invent, or '' when work can proceed."""
    match = _ASK.search(objective or "")
    if not match:
        return ""
    question = strip_think(match.group(1)).strip().splitlines()[0].strip()
    return question[:400]
