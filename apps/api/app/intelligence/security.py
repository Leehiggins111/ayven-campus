"""Web content is untrusted. It cannot grant permissions or rewrite the objective."""

from __future__ import annotations

import re

_INJECTION = re.compile(
    r"(ignore (all|any|previous) instructions|system prompt|you are now|exfiltrate|send the (password|secret)|developer message)",
    re.I,
)


def fence(kind: str, text: str) -> str:
    body = (text or "").replace("</ayven_untrusted>", "")
    return f'<ayven_untrusted kind="{kind}">\n{body}\n</ayven_untrusted>'


def partition(system: str, objective: str, observation: str = "", web: str = "") -> dict:
    """Keep the four channels apart. Web text is fenced and is not the system prompt."""
    return {
        "system": system or "",
        "objective": objective or "",
        "observation": fence("observation", observation) if observation else "",
        "web": fence("web", web) if web else "",
    }


def injection_signals(text: str) -> list[str]:
    return [match.group(0) for match in _INJECTION.finditer(text or "")]


def objective_held(original: str, candidate: str) -> bool:
    """A page cannot replace the objective. Equality of the stored objective is the check."""
    return (original or "").strip() == (candidate or "").strip()


def llm_guard_decision() -> dict:
    return {
        "project": "protectai/llm-guard",
        "decision": "OPTIONAL",
        "installed": False,
        "reason": "The project is maintained, but it pulls scanner models and a heavy stack. Ayven fences untrusted web text and tests injection without that dependency. Rebuff is archived and is not used.",
    }
