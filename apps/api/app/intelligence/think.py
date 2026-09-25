"""Remove hidden chain-of-thought before anything is stored or shown."""

from __future__ import annotations

import re

_CLOSED = re.compile(
    r"(?is)<\s*(think|redacted_thinking|reasoning)\b[^>]*>.*?<\s*/\s*\1\s*>"
)
_OPEN = re.compile(r"(?is)<\s*(think|redacted_thinking|reasoning)\b[^>]*>.*")


def strip_think(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _CLOSED.sub("", text)
    cleaned = _OPEN.sub("", cleaned)
    return cleaned.strip()


def contains_think(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return "<think" in lowered or "<redacted_thinking" in lowered or "<reasoning" in lowered
