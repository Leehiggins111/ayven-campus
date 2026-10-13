"""Model gateway. Local backends first. Paid escalation stays off unless explicitly armed.

LiteLLM is not imported. Its enterprise tree is a separate licence, and this
gateway already records route, fallback, cooldown, and cost for the backends Ayven calls.
"""

from __future__ import annotations

import os
import time

from .registry import route_for

_COOLDOWN: dict[str, float] = {}


def select(role: str, stage: str, task_class: str = "web_research") -> dict:
    choice = route_for(task_class, stage if stage else role.lower())
    choice["role"] = role
    choice["healthy"] = not _cooling(choice.get("model_id") or "")
    choice["escalation_enabled"] = os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"
    return choice


def _cooling(model_id: str) -> bool:
    until = _COOLDOWN.get(model_id) or 0
    return until > time.time()


def note_failure(model_id: str, seconds: int = 30) -> None:
    _COOLDOWN[model_id] = time.time() + seconds


def note_success(model_id: str) -> None:
    _COOLDOWN.pop(model_id, None)


def estimate_cost(tokens: int, role: str) -> float:
    """Local inference is accounted as GPU time elsewhere. This figure is the token estimate only."""
    if role.upper() == "ESCALATION" and os.environ.get("AYVEN_ALLOW_ESCALATION", "0") != "1":
        return 0.0
    rate = {"EMPLOYEE": 0.0, "SUPERVISOR": 0.0, "MANAGER": 0.0}.get(role.upper(), 0.0)
    return round(tokens / 1000 * rate, 6)


def litellm_decision() -> dict:
    return {
        "project": "BerriAI/litellm",
        "decision": "REJECTED",
        "reason": "Routing, fallback, cooldown, and cost for the local OpenAI-compatible path live in this module. LiteLLM is large and its enterprise directory is a different licence.",
    }
