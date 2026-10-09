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
    conn = connect()
    if idempotency_key:
        existing = conn.execute(
            "SELECT id, status FROM projects WHERE title=?",
            (f"milo:{idempotency_key}",),
        ).fetchone()
        if existing:
            conn.close()
            return {"project_id": existing["id"], "duplicate": True, "status": existing["status"]}
    project_id = str(uuid.uuid4())
    title = f"milo:{idempotency_key}" if idempotency_key else objective[:80]
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, title, objective, "running", _now()),
    )
    conn.commit()
    conn.close()
    events.emit("project.created", project_id=project_id, agent_id="milo", department_id="command", status="running", summary="Milo submitted a job")
    orchestrator.run_project(project_id)
    return {"project_id": project_id, "duplicate": False, "status": "running"}


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
        "engine": "not_connected",
        "research_mode": "fixtures_unless_AYVEN_RESEARCH_MODE=live",
    }
