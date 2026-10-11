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


def submit_job(objective: str, idempotency_key: str = "", title: str | None = None) -> dict:
    from fastapi import HTTPException
    objective = objective.strip()
    idempotency_key = idempotency_key.strip()
    if not objective:
        raise HTTPException(400, "Objective is empty")
    if len(idempotency_key) > 200:
        raise HTTPException(400, "Idempotency key is too long")
    project_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "ayven-project:" + idempotency_key)) if idempotency_key else str(uuid.uuid4())
    title = title or objective[:80]
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    if idempotency_key:
        existing = conn.execute(
            "SELECT id, status, objective, title FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if existing:
            conn.close()
            if existing["objective"] != objective or existing["title"] != title:
                raise HTTPException(409, "Idempotency key already belongs to another request")
            return {"project_id": existing["id"], "duplicate": True, "status": existing["status"]}
    else:
        for existing in conn.execute("SELECT id, status, objective FROM projects WHERE status IN ('running','waiting')").fetchall():
            if " ".join(existing["objective"].split()).casefold() == " ".join(objective.split()).casefold():
                conn.close()
                return {"project_id": existing["id"], "duplicate": True, "status": existing["status"]}
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
    from .intelligence.web_artifact import verified_web, artifact_files
    research = {}
    try:
        research = json.loads(package.get("research_json") or "{}")
        obs = json.loads(package.get("observability_json") or "{}")
        research["web_execution"] = research.get("web_execution") or obs.get("web_execution") or {}
        research["mode"] = research.get("mode") or obs.get("research_mode") or "unknown"
        web_ready = verified_web(package.get("findings") or "", research)
    except (ValueError, TypeError, AttributeError):
        web_ready = False
    return {
        "project_id": project_id,
        "status": project["status"],
        "package_id": package.get("id"),
        "workflow_state": package.get("workflow_state"),
        "question": package.get("clarification_question") or "",
        "waiting_for_lee": package.get("workflow_state") in {"AWAITING_CLARIFICATION", "AWAITING_APPROVAL"},
        "approval": dict(approval) if approval else None,
        "result": package.get("findings") or project["result"],
        "engine": "ayven-local",
        "research_mode": research.get("mode") if isinstance(research, dict) else "unknown",
        "web_artifact_verified": web_ready,
        "artifacts": [{"name": name, "content": source} for name, source in artifact_files(package.get("findings") or "").items()] if web_ready else [],
    }
