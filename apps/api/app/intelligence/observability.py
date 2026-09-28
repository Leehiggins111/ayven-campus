"""Local structured traces. A remote sink is optional and off by default."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def trace(event: str, **fields) -> dict:
    row = {"at": _now(), "event": event, **fields}
    path = os.environ.get("AYVEN_TRACE_PATH", "")
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    return row


def summarise(observability: dict) -> dict:
    """The campus reads this. It is not a log wall."""
    repairs = observability.get("repairs") or []
    queries = observability.get("queries") or []
    pages = observability.get("pages") or []
    opened = [page for page in pages if page]
    relevant = observability.get("relevant_opened")
    precision = None
    if opened:
        precision = round((relevant if isinstance(relevant, int) else len(opened)) / len(opened), 2)
    return {
        "doing": observability.get("manager_decision") or "working",
        "why": (observability.get("resolution") or {}).get("reason") or "",
        "stuck": bool(observability.get("errors")),
        "needs_you": (observability.get("manager_decision") in ("CLARIFY", "APPROVAL_REQUIRED", "ESCALATE")),
        "finished": observability.get("manager_decision") == "SYNTHESISE",
        "repairs": len(repairs),
        "queries": len(queries),
        "sources": len(opened),
        "research_precision": precision,
        "tokens": observability.get("tokens") or 0,
        "errors": observability.get("errors") or [],
    }


def langfuse_decision() -> dict:
    return {
        "project": "langfuse/langfuse",
        "decision": "OPTIONAL",
        "installed": False,
        "reason": "Local JSON traces are the mandatory sink. Langfuse is MIT and fits as a remote backend when AYVEN_LANGFUSE_HOST is set. It is not required to run Campus or the exams.",
    }
