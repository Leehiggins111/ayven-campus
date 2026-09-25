from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from . import events
from .db import connect
from .llm import complete
from .researcher import run_package
from .workforce import run_objective as run_workforce

COST_PER_1K = 0.002


def _set_agent(agent_id: str, **fields) -> None:
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()


def run_project(project_id: str) -> None:
    threading.Thread(target=_run_project, args=(project_id,), daemon=True).start()


def _run_project(project_id: str) -> None:
    conn = connect()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    conn.close()
    if not project:
        return
    objective = project["objective"]
    _set_agent("milo", status="working", last_summary="Deciding required work", progress=0.15)
    events.emit("agent.started_task", project_id=project_id, agent_id="milo", department_id="command", status="working", summary="Milo is scoping the objective for Research Lab", progress=0.15)
    brief, tokens = complete("You are Milo, Ayven Chief of Staff. Write a short research brief. No chain-of-thought.", f"Lee's objective:\n{objective}", max_tokens=280)
    conn = connect()
    conn.execute("UPDATE agents SET tokens=tokens+?, cost_usd=cost_usd+? WHERE id=?", (tokens, tokens / 1000 * COST_PER_1K, "milo"))
    conn.commit()
    conn.close()
    tid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    conn = connect()
    conn.execute("INSERT INTO tasks(id,project_id,agent_id,title,brief,status,requires_approval,created_at) VALUES(?,?,?,?,?,?,?,?)", (tid, project_id, "research-mgr", "Research programme", brief, "queued", 0, ts))
    conn.execute("INSERT INTO work_packages(id,project_id,task_id,title,objective,origin,agent_id,department_id,stage,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (pid, project_id, tid, "Research programme", objective, "milo", "research-mgr", "research", "command", "assigned", ts, ts))
    conn.commit()
    conn.close()
    events.emit("task.created", project_id=project_id, task_id=tid, agent_id="research-mgr", department_id="research", status="queued", summary="Task created: Research programme")
    events.emit("package.created", project_id=project_id, task_id=tid, agent_id="milo", department_id="command", status="assigned", summary="Handed to Research Manager")
    _set_agent("milo", status="idle", last_summary="Waiting on Research Lab", progress=0.4)
    try:
        run_workforce(project_id, objective, tid)
    except Exception:
        run_package(pid)
    conn = connect()
    pkg = conn.execute("SELECT * FROM work_packages WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchone()
    findings = pkg["findings"] if pkg else brief
    conn.execute("UPDATE projects SET status=?, result=? WHERE id=?", ("complete", findings, project_id))
    conn.commit()
    conn.close()
    _set_agent("milo", status="idle", last_summary="Brief ready", progress=1)
    events.emit("milo.synthesized", project_id=project_id, agent_id="milo", department_id="command", status="idle", summary="Milo published Research Lab findings", progress=1)


def resolve_approval(approval_id: str, decision: str) -> None:
    conn = connect()
    row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        raise KeyError("approval not found")
    conn.execute("UPDATE approvals SET status=? WHERE id=?", (decision, approval_id))
    if row["task_id"]:
        conn.execute("UPDATE tasks SET approval_status=?, status=? WHERE id=?", (decision, "complete" if decision == "approved" else "rejected", row["task_id"]))
        conn.execute("UPDATE work_packages SET status=?, stage=?, destination=? WHERE task_id=?", ("complete" if decision == "approved" else "rejected", "results" if decision == "approved" else "exception", "command" if decision == "approved" else "exception", row["task_id"]))
    conn.commit()
    agent_id = row["agent_id"]
    conn.close()
    _set_agent(agent_id, status="idle", last_summary=f"Approval {decision}", progress=1, current_task_id=None)
    events.emit("approval.resolved", project_id=row["project_id"], task_id=row["task_id"], agent_id=agent_id, department_id="command", status=decision, summary=f"Lee {decision} the enquiry draft — nothing was sent automatically")


def fail_demo(agent_id: str = "web-researcher") -> None:
    _set_agent(agent_id, status="error", last_summary="Simulated tool timeout")
    events.emit("agent.failed", agent_id=agent_id, department_id="research", status="error", summary="Simulated failure: tool timeout. Retry available.")


def retry_agent(agent_id: str) -> None:
    _set_agent(agent_id, status="idle", last_summary="Recovered after failure")
    events.emit("agent.idle", agent_id=agent_id, department_id="research", status="idle", summary="Agent recovered and is idle")
