from __future__ import annotations

from datetime import datetime, timezone

from . import events
from .db import connect


def _set_agent(agent_id: str, **fields) -> None:
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()


def run_project(project_id: str) -> None:
    """Start a job on the durable workflow. The thread stub does not run it."""
    from .durable import enqueue_project, thread_stub_armed

    if thread_stub_armed():
        return
    enqueue_project(project_id)


def _run_project(project_id: str) -> None:
    raise RuntimeError("Thread execution is not available. Jobs run through the durable workflow.")


def finalize_project(project_id: str) -> None:
    conn = connect()
    pkg = conn.execute(
        "SELECT * FROM work_packages WHERE project_id=? AND tier='MANAGER' AND findings IS NOT NULL ORDER BY updated_at DESC",
        (project_id,),
    ).fetchone()
    if not pkg:
        pkg = conn.execute("SELECT * FROM work_packages WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchone()
    findings = pkg["findings"] if pkg else ""
    state = (pkg["workflow_state"] if pkg and pkg["workflow_state"] else "") or ""
    if state in ("AWAITING_APPROVAL", "AWAITING_CLARIFICATION", "ESCALATED"):
        project_status = "waiting"
    elif state == "FAILED":
        project_status = "rejected"
    elif state == "UNRESOLVED":
        project_status = "unresolved"
    elif state == "COMPLETED":
        project_status = "complete"
    elif state == "UNRESOLVED":
        project_status = "unresolved"
    else:
        project_status = "running"
    if pkg and findings and state != "FAILED":
        # Generated files go into the file store, or bundle.zip has nothing to serve.
        from .blobs import store_package_files

        row = dict(pkg)
        store_package_files(
            row.get("parent_id") or row["id"], findings,
            row.get("research_json") or "", row.get("observability_json") or "",
        )
    if pkg and state in ("AWAITING_APPROVAL", "FAILED", "UNRESOLVED", "COMPLETED", "APPROVED"):
        # Acceptance criteria are checked against what was delivered, so criteria_results is never left empty.
        from .goals import evaluate_criteria

        evaluate_criteria(project_id, findings or "")
    conn.execute("UPDATE projects SET status=?, result=? WHERE id=?", (project_status, findings, project_id))
    conn.execute("UPDATE tasks SET status=? WHERE id=?", (project_status, f"engine:{project_id}"))
    conn.commit()
    conn.close()
    if state == "AWAITING_CLARIFICATION":
        _set_agent("milo", status="waiting", visual_state="WAITING", last_summary="Needs clarification", progress=0.5)
    elif state in ("AWAITING_APPROVAL", "ESCALATED"):
        _set_agent("milo", status="needs_approval", visual_state="NEEDS_APPROVAL", last_summary="Needs Lee", progress=0.8)
    elif state == "FAILED":
        _set_agent("milo", status="error", visual_state="FAILED", last_summary="Package failed", progress=1)
    elif state == "COMPLETED":
        _set_agent("milo", status="idle", visual_state="COMPLETED", last_summary="Brief ready", progress=1)
    else:
        _set_agent("milo", status="idle", visual_state="IDLE", last_summary="Brief ready", progress=1)
    events.emit("milo.synthesized", project_id=project_id, agent_id="milo", department_id="command", status=state or "idle", summary="Milo updated the campus from the work package", progress=1)


def resolve_approval(approval_id: str, decision: str) -> None:
    conn = connect()
    row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        raise KeyError("approval not found")
    if row["status"] != "pending":
        conn.close()
        if row["status"] != decision:
            raise ValueError("approval already resolved with another decision")
        return
    conn.execute("UPDATE approvals SET status=? WHERE id=?", (decision, approval_id))
    if row["task_id"]:
        conn.execute("UPDATE tasks SET approval_status=?, status=? WHERE id=?", (decision, "complete" if decision == "approved" else "rejected", row["task_id"]))
        conn.execute("UPDATE work_packages SET status=?, stage=?, destination=? WHERE task_id=?", ("complete" if decision == "approved" else "rejected", "results" if decision == "approved" else "exception", "command" if decision == "approved" else "exception", row["task_id"]))
    waiting = conn.execute(
        "SELECT id FROM work_packages WHERE workflow_state='AWAITING_APPROVAL' AND (task_id=? OR project_id=?)",
        (row["task_id"], row["project_id"]),
    ).fetchall()
    conn.commit()
    agent_id = row["agent_id"]
    conn.close()
    from .intelligence.workflow import log_transition

    landed = "APPROVED" if decision == "approved" else "FAILED"
    for pkg in waiting:
        log_transition(pkg["id"], landed, f"approval {decision}; no external action was taken")
        if decision == "rejected":
            from .distribution import update_package

            update_package(pkg["id"], campus_stage="REJECTED", status="rejected", stage="exception")
    if decision == "rejected" and waiting:
        _set_agent(agent_id, status="error", visual_state="FAILED", last_summary="Lee rejected the package", progress=1, current_task_id=None)
        if row["project_id"]:
            conn = connect()
            conn.execute("UPDATE projects SET status=? WHERE id=?", ("rejected", row["project_id"]))
            conn.commit()
            conn.close()
    elif decision == "approved" and waiting:
        _set_agent(agent_id, status="idle", visual_state="WAITING", last_summary="Approval recorded", progress=0.9, current_task_id=None)
    else:
        _set_agent(agent_id, status="idle", last_summary=f"Approval {decision}", progress=1, current_task_id=None)
    events.emit("approval.resolved", project_id=row["project_id"], task_id=row["task_id"], agent_id=agent_id, department_id="command", status=decision, summary=f"Lee {decision} the enquiry draft — nothing was sent automatically")


def fail_demo(agent_id: str = "web-researcher") -> None:
    _set_agent(agent_id, status="error", visual_state="FAILED", last_summary="Simulated tool timeout")
    events.emit("agent.failed", agent_id=agent_id, department_id="research", status="FAILED", summary="Simulated failure: tool timeout. Retry available.")


def retry_agent(agent_id: str) -> None:
    _set_agent(agent_id, status="idle", visual_state="IDLE", last_summary="Recovered after failure")
    events.emit("agent.idle", agent_id=agent_id, department_id="research", status="IDLE", summary="Agent recovered and is idle")
