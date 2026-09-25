"""Authoritative supervisor decision. The model may advise; it does not outvote the checks."""

from __future__ import annotations

import os
import re

from .think import contains_think

_LIVE = ("tickets are available", "available to buy now", "in stock now", "we can purchase")


def asserts_unsupported_final_quote(text: str) -> bool:
    """True when the briefing adopts one total as the customer quote."""
    sample = text or ""
    if re.search(r"the final quote is\s+£", sample, re.I):
        return True
    if re.search(r"\bquote is final\b", sample, re.I):
        return True
    if re.search(r"final price is\s+£", sample, re.I):
        return True
    return False


def claims_live_availability(text: str) -> bool:
    lowered = (text or "").lower()
    if "live availability: not claimed" in lowered and not any(phrase in lowered for phrase in _LIVE):
        return False
    return any(phrase in lowered for phrase in _LIVE)


def authoritative_decision(report: str, task_class: str, focus: str, attempt: int) -> str:
    limit = int(os.environ.get("AYVEN_MAX_ATTEMPTS", "2"))
    if contains_think(report):
        return "TAKE_OVER"
    if asserts_unsupported_final_quote(report):
        return "TAKE_OVER"
    if claims_live_availability(report):
        return "TAKE_OVER"
    text = (report or "").lower()
    if focus == "scenarios":
        if "1533.00" not in report or "963.00" not in report:
            return "RETURN" if attempt < limit else "TAKE_OVER"
        if "ambiguous" not in text:
            return "TAKE_OVER"
        if "not applied" not in text and "unknown_not_applied" not in text:
            return "TAKE_OVER"
        if "not a final quote" not in text:
            return "TAKE_OVER"
        return "ACCEPT"
    if focus == "gaps":
        if task_class == "internal_door_quote" and "handing" not in text:
            return "TAKE_OVER"
        if "gap" not in text and "missing" not in text and "unknown" not in text:
            return "RETURN" if attempt < limit else "TAKE_OVER"
        return "ACCEPT"
    if focus in ("routes", "channels", "evidence"):
        if task_class == "football_tickets" and "official" not in text:
            return "RETURN" if attempt < limit else "TAKE_OVER"
        if task_class == "internal_door_quote" and "no supplier is named" not in text and "http" not in text:
            return "TAKE_OVER"
        return "ACCEPT"
    if focus in ("prospects", "draft"):
        if "nothing was sent" not in text and "sent: no" not in text:
            return "TAKE_OVER"
        return "ACCEPT"
    if focus == "trivial":
        return "ACCEPT"
    return "ACCEPT"


def advisory_decision(model_text: str) -> str:
    upper = (model_text or "").upper()
    for token in ("TAKE_OVER", "TAKE OVER", "ESCALATE", "RETURN", "ACCEPT"):
        if token in upper:
            return "TAKE_OVER" if token.startswith("TAKE") else ("ESCALATE" if token == "ESCALATE" else token)
    return ""
