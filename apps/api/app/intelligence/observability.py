"""Local structured traces. A remote sink is optional and off by default."""

from __future__ import annotations

import contextvars
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

_TRACE: contextvars.ContextVar[str] = contextvars.ContextVar("ayven_trace_id", default="")


def current_trace() -> str:
    return _TRACE.get()


def bind_trace(trace_id: str):
    return _TRACE.set(trace_id or "")


def reset_trace(token) -> None:
    _TRACE.reset(token)


def new_trace_id() -> str:
    return str(uuid.uuid4())


def redact(text: str) -> str:
    canary = os.environ.get("AYVEN_SECRET_CANARY", "")
    if canary and canary in (text or ""):
        return text.replace(canary, "[REDACTED]")
    return text or ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_trace(package_id: str, event: str, payload: dict | None = None) -> dict:
    """Always persist a structured trace for a work package. A file sink is optional."""
    import uuid

    from ..db import connect

    safe = {key: redact(value) if isinstance(value, str) else value for key, value in (payload or {}).items()}
    safe.pop("trace_id", None)
    row = trace(event, package_id=package_id, trace_id=current_trace(), **safe)
    conn = connect()
    conn.execute(
        "INSERT INTO traces(id,package_id,event,payload,created_at,trace_id) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), package_id, event, redact(json.dumps(row, default=str)), row["at"], current_trace()),
    )
    conn.commit()
    conn.close()
    return row


def traces_for(package_id: str) -> list[dict]:
    from ..db import connect

    conn = connect()
    rows = conn.execute(
        "SELECT id, package_id, event, payload, created_at FROM traces WHERE package_id=? ORDER BY created_at",
        (package_id,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item["payload"] or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        out.append(item)
    return out


def trace(event: str, **fields) -> dict:
    row = {"at": _now(), "event": event, "trace_id": fields.get("trace_id") or current_trace(), **fields}
    path = os.environ.get("AYVEN_TRACE_PATH", "")
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(redact(json.dumps(row, default=str)) + "\n")
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


def run_metrics(observability: dict) -> dict:
    pages = [page for page in (observability.get("pages") or []) if page]
    relevant = observability.get("relevant_opened") or 0
    challenges = observability.get("material_challenges") or []
    disproved = [row for row in challenges if row.get("result") in ("DISPROVED", "UNSUPPORTED")]
    repairs = observability.get("repairs") or []
    false_rejects = [row for row in repairs if row.get("false_rejection")]
    precision = round(relevant / len(pages), 3) if pages else None
    catch = round(len(disproved) / len(challenges), 3) if challenges else None
    false_rate = round(len(false_rejects) / len(disproved), 3) if disproved else 0.0
    return {
        "research_precision": precision,
        "repair_rate": observability.get("repair_rate"),
        "supervisor_catch_rate": catch,
        "false_rejection_rate": false_rate,
        "retries": observability.get("retries") or 0,
        "sources": len(pages),
        "challenged": len(challenges),
        "disproved": len(disproved),
    }


def langfuse_decision() -> dict:
    return {
        "project": "langfuse/langfuse",
        "decision": "OPTIONAL",
        "installed": False,
        "reason": "Local JSON traces are the mandatory sink. Langfuse is MIT and fits as a remote backend when AYVEN_LANGFUSE_HOST is set. It is not required to run Campus or the exams.",
    }
