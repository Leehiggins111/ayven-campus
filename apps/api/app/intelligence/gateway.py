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


def estimate_cost(tokens: int, role: str, input_tokens: int | None = None, output_tokens: int | None = None) -> float:
    """USD for this call. Local roles use the GPU hourly rate. Escalation uses explicit prices."""
    from .budget import estimate_usd

    incoming = input_tokens if input_tokens is not None else tokens
    outgoing = output_tokens if output_tokens is not None else max(int(tokens) // 4, 1)
    if role.upper() == "ESCALATION" and os.environ.get("AYVEN_ALLOW_ESCALATION", "0") != "1":
        return 0.0
    return estimate_usd(role, input_tokens=int(incoming or 0), output_tokens=int(outgoing or 0))


def before_call(job_id: str, role: str, max_tokens: int, **kwargs) -> dict:
    from .budget import check_and_reserve

    return check_and_reserve(job_id, role, max_tokens, **kwargs)


def after_call(reservation_id: str, **kwargs) -> dict:
    from .budget import commit

    return commit(reservation_id, **kwargs)


def litellm_decision() -> dict:
    return {
        "project": "BerriAI/litellm",
        "decision": "REJECTED",
        "reason": "Routing, fallback, cooldown, and cost for the local OpenAI-compatible path live in this module. LiteLLM is large and its enterprise directory is a different licence.",
    }
