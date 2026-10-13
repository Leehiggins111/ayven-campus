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


_REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking", "reasoning_details")


def visible_message(message: dict | None) -> str:
    """Use only the visible content. A reasoning field is never copied into the answer."""
    message = message or {}
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)
    return strip_think(content)
