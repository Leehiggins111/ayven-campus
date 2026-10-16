"""Campus side of the Milo handoff. The engine calls these; Campus does not invent an engine."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from .db import connect
from . import events


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def constraint_list(value) -> list[str]:
    """Milo may send a string or a list. Blank items are dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()][:20]
    text = str(value).strip()
    return [text] if text else []


def current_run_mode() -> str:
    """The mode of this process. REAL is not inferred from a missing flag."""
    if os.environ.get("AYVEN_DEMO") == "1":
        return "DEMO"
    if os.environ.get("AYVEN_DRY_RUN") == "1":
        return "DRY_RUN"
    stub = os.environ.get("AYVEN_LLM_STUB", "1") != "0"
    fixtures = os.environ.get("AYVEN_RESEARCH_MODE", "fixtures") != "live"
    if stub and fixtures:
        return "SIMULATED"
    if stub or fixtures:
        return "PARTLY_SIMULATED"
    return "UNVERIFIED"


def hard_constraint_text(items: list[str]) -> str:
    clean = constraint_list(items)
    if not clean:
        return ""
    lines = "\n".join(f"- {item}" for item in clean)
    return "Hard constraints. Do not relax, skip, or reinterpret these:\n" + lines


def job_constraints(project_id: str) -> list[str]:
    if not project_id:
        return []
    conn = connect()
    row = conn.execute("SELECT constraints_json FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row or not row["constraints_json"]:
        return []
    try:
        return constraint_list(json.loads(row["constraints_json"]))
    except (TypeError, ValueError):
        return []


def submit_job(objective: str, idempotency_key: str = "", title: str | None = None, constraints=None) -> dict:
    from fastapi import HTTPException
    objective = objective.strip()
    idempotency_key = idempotency_key.strip()
    if not objective:
        raise HTTPException(400, "Objective is empty")
    if len(idempotency_key) > 200:
        raise HTTPException(400, "Idempotency key is too long")
    held = constraint_list(constraints)
    mode = current_run_mode()
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
            # A new title on the same key is the same job. A different objective is not.
            # A different key is a different id, so an explicit re-run is inserted below.
            if existing["objective"] != objective:
                raise HTTPException(409, "Idempotency key already belongs to another request")
            return {
                "project_id": existing["id"],
                "duplicate": True,
                "status": existing["status"],
                "mode": _stored_mode(existing["id"]),
                "constraints": job_constraints(existing["id"]),
            }
    else:
        for existing in conn.execute("SELECT id, status, objective FROM projects WHERE status IN ('running','waiting')").fetchall():
            if " ".join(existing["objective"].split()).casefold() == " ".join(objective.split()).casefold():
                conn.close()
                return {
                    "project_id": existing["id"],
                    "duplicate": True,
                    "status": existing["status"],
                    "mode": _stored_mode(existing["id"]),
                    "constraints": job_constraints(existing["id"]),
                }
    conn.execute(
        """INSERT INTO projects(id,title,objective,status,created_at,constraints_json,run_mode)
           VALUES(?,?,?,?,?,?,?)""",
        (project_id, title, objective, "running", _now(), json.dumps(held), mode),
    )
    conn.commit()
    conn.close()
    events.emit("project.created", project_id=project_id, agent_id="milo", department_id="command", status="running", summary="Milo submitted a job")
    return {"project_id": project_id, "duplicate": False, "status": "running", "mode": mode, "constraints": held}


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
    from .goals import criteria_evidence
    from .outcome import outcome_for, result_text

    outcome = outcome_for(project_id=project_id)
    execution = None
    from .workbench_bridge import ensure_tables
    ec = connect()
    try:
        ensure_tables(ec)
        er = ec.execute("SELECT id,state,result_json FROM workbench_orders WHERE project_id=?", (project_id,)).fetchone()
        if er:
            execution = {"order_id": er["id"], "state": er["state"],
                         "result": json.loads(er["result_json"]) if er["result_json"] else None}
    finally:
        ec.close()
    if execution:
        # Workbench orders have their own approval lifecycle. Do not project
        # a still-running Campus project as a successful completed deliverable.
        er_state = execution["state"]
        report = execution["result"] or {}
        safe_status = {
            "queued": "queued", "running": "running",
            "awaiting_approval": "waiting", "completed": "completed",
            "rejected": "rejected", "failed": "rejected",
        }.get(er_state, "blocked")
        return {
            "project_id": project_id, "execution": execution,
            "status": safe_status, "package_id": None,
            "workflow_state": "AWAITING_APPROVAL" if er_state == "awaiting_approval" else er_state.upper(),
            "question": "", "waiting_for_lee": er_state == "awaiting_approval",
            "approval": {"status": "pending", "summary": "Workbench result awaits owner decision"} if er_state == "awaiting_approval" else None,
            "result": report.get("defects", []) if er_state in ("failed", "rejected") else report.get("evidence", []),
            "outcome": {"verdict": report.get("verdict", "UNVERIFIED"), "execution_mode": report.get("execution_mode", "UNVERIFIED")},
            "criteria_evidence": [], "engine": "workbench",
            "research_mode": "unknown", "web_artifact_verified": False,
            "artifacts": [{"name": a["name"], "sha256": a["sha256"]} for a in report.get("artifacts", [])] if er_state == "completed" else [],
            "recovery": {"state": er_state}, "mode": report.get("execution_mode", "PENDING_REAL"),
            "constraints": job_constraints(project_id), "criteria_results": _criteria(project_id),
            **_budget(project_id),
        }
    return {
        "project_id": project_id,
        "execution": execution,
        "status": project["status"],
        "package_id": package.get("id"),
        "workflow_state": package.get("workflow_state"),
        "question": package.get("clarification_question") or "",
        "waiting_for_lee": package.get("workflow_state") in {"AWAITING_CLARIFICATION", "AWAITING_APPROVAL"},
        "approval": dict(approval) if approval else None,
        # Rejected, failed or blocked work carries its label, so it is never shown as a deliverable.
        "result": result_text(outcome, package.get("findings") or project["result"] or ""),
        "outcome": outcome,
        "criteria_evidence": criteria_evidence(project_id),
        "engine": "ayven-local",
        "research_mode": research.get("mode") if isinstance(research, dict) else "unknown",
        "web_artifact_verified": web_ready,
        "artifacts": [{"name": name, "content": source} for name, source in artifact_files(package.get("findings") or "").items()] if web_ready else [],
        "recovery": _recovery(project_id),
        "mode": (project["run_mode"] or "").strip() or "UNVERIFIED",
        "constraints": job_constraints(project_id),
        "criteria_results": _criteria(project_id),
        **_budget(project_id),
    }


def _budget(project_id: str) -> dict:
    """The job's spending cap, as stored, so the status shows what Lee set."""
    from .goals import get_goal

    goal = get_goal(project_id) or {}
    return {
        "job_cap_gbp": goal.get("job_cap_gbp"),
        "job_cap_usd": goal.get("job_cap_usd"),
        "spent_gbp": goal.get("spent_gbp"),
    }


def _criteria(project_id: str) -> list[dict]:
    from .goals import criteria_for

    return criteria_for(project_id)


def _stored_mode(project_id: str) -> str:
    conn = connect()
    row = conn.execute("SELECT run_mode FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return "UNVERIFIED"
    return (row["run_mode"] or "").strip() or "UNVERIFIED"


def _recovery(project_id: str) -> dict:
    from .intelligence.leases import lease_view

    row = lease_view(project_id)
    if not row:
        return {"state": "none"}
    return {"state": row["state"], "attempts": row["attempts"], "fence": row["fence"]}
