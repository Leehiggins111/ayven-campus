"""Campus side of the Milo handoff. The engine calls these; Campus does not invent an engine."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import connect
from . import events
from . import orchestrator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_job(objective: str, idempotency_key: str = "") -> dict:
    from .main import ObjectiveIn, _submit_project
    receipt = _submit_project(ObjectiveIn(objective=objective), idempotency_key)
    return {**receipt, "duplicate": receipt["replayed"]}


def job_status(project_id: str) -> dict:
    conn = connect()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        raise KeyError(project_id)
    package = conn.execute(
        "SELECT * FROM work_packages WHERE project_id=? AND parent_id IS NULL ORDER BY updated_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    approval = conn.execute(
        "SELECT id, status, summary FROM approvals WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    conn.close()
    package = dict(package) if package else {}
    return {
        "project_id": project_id,
        "status": project["status"],
        "package_id": package.get("id"),
        "workflow_state": package.get("workflow_state"),
        "question": package.get("clarification_question") or "",
        "waiting_for_lee": package.get("workflow_state") in {"AWAITING_CLARIFICATION", "AWAITING_APPROVAL"},
        "approval": dict(approval) if approval else None,
        "result": package.get("findings") or project["result"],
        "engine": "durable" if __import__("os").environ.get("AYVEN_DURABLE") == "1" else "ephemeral",
        "research_mode": __import__("os").environ.get("AYVEN_RESEARCH_MODE", "fixtures"),
    }
