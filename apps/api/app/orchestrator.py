from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone

from . import events
from .db import connect
from .llm import complete
from .tools import web_search

COST_PER_1K = 0.002  # bookkeeping estimate only


def _set_agent(agent_id: str, **fields) -> None:
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()


def _dept(agent_id: str) -> str:
    conn = connect()
    row = conn.execute("SELECT department_id FROM agents WHERE id=?", (agent_id,)).fetchone()
    conn.close()
    return row["department_id"] if row else "command"


def run_project(project_id: str) -> None:
    threading.Thread(target=_run_project, args=(project_id,), daemon=True).start()


def _run_project(project_id: str) -> None:
    conn = connect()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return
    objective = project["objective"]
    conn.close()

    _set_agent("milo", status="working", last_summary="Decomposing objective", progress=0.1)
    events.emit(
        "agent.started_task",
        project_id=project_id,
        agent_id="milo",
        department_id="command",
        status="working",
        summary="Milo is decomposing the objective into workstreams",
        progress=0.1,
    )

    plan_text, tokens = complete(
        "You are Milo, Ayven Chief of Staff. Output a short plan only. No hidden reasoning.",
        f"Decompose this objective into research workstreams:\n{objective}",
    )
    _add_tokens("milo", tokens)
    _remember("project:" + project_id, f"Plan:\n{plan_text}")

    workstreams = [
        (
            "ticket-researcher",
            "Official ticket channels",
            "Research official / authorised ticket channels for Dortmund, Ajax, Sparta Prague and Rosenborg. Do not recommend touting or inventory speculation.",
            "Dortmund Ajax Sparta Prague Rosenborg official tickets authorised seller",
            False,
        ),
        (
            "supplier-researcher",
            "Authorised sports travel wholesalers",
            "Find authorised sports travel wholesalers / package organisers that might supply match access without Ayven buying inventory upfront.",
            "authorised football travel wholesaler official package operator Europe",
            False,
        ),
        (
            "compliance",
            "Resale and package-travel considerations",
            "Summarise high-level legal themes around unauthorised ticket resale vs authorised packages. Not legal advice.",
            "EU package travel regulations football ticket resale authorised seller",
            False,
        ),
        (
            "outreach",
            "Draft supplier enquiry (approval required)",
            "Draft a short enquiry to an authorised wholesaler. Do not send. Hold for Lee approval.",
            None,
            True,
        ),
    ]

    task_ids = []
    for agent_id, title, brief, _, needs_appr in workstreams:
        tid = str(uuid.uuid4())
        task_ids.append(tid)
        conn = connect()
        conn.execute(
            """INSERT INTO tasks(id,project_id,agent_id,title,brief,status,requires_approval,approval_status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                tid,
                project_id,
                agent_id,
                title,
                brief,
                "queued",
                1 if needs_appr else 0,
                "pending" if needs_appr else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        events.emit(
            "task.created",
            project_id=project_id,
            task_id=tid,
            agent_id=agent_id,
            department_id=_dept(agent_id),
            status="queued",
            summary=f"Task created: {title}",
        )
        events.emit(
            "task.assigned",
            project_id=project_id,
            task_id=tid,
            agent_id=agent_id,
            department_id=_dept(agent_id),
            status="queued",
            summary=f"Assigned to {agent_id}",
        )

    findings = []
    for tid, (agent_id, title, brief, query, needs_appr) in zip(task_ids, workstreams):
        findings.append(_execute_task(project_id, tid, agent_id, title, brief, query, needs_appr))

    ready = [f for f in findings if f]
    synth, tok = complete(
        "You are Milo. Consolidate worker findings into a brief for Lee. No chain-of-thought.",
        "Consolidate these findings into a go/no-go style briefing:\n" + "\n\n".join(ready),
    )
    _add_tokens("milo", tok)
    conn = connect()
    conn.execute("UPDATE projects SET status=?, result=? WHERE id=?", ("complete", synth, project_id))
    conn.commit()
    conn.close()
    _set_agent("milo", status="idle", last_summary="Brief ready", progress=1, current_task_id=None)
    events.emit(
        "milo.synthesized",
        project_id=project_id,
        agent_id="milo",
        department_id="command",
        status="idle",
        summary="Milo published a consolidated briefing",
        progress=1,
    )
    _remember("company", f"Project {project_id} briefing stored.")


def _execute_task(
    project_id: str,
    task_id: str,
    agent_id: str,
    title: str,
    brief: str,
    query: str | None,
    needs_approval: bool,
) -> str:
    dept = _dept(agent_id)
    _set_agent(agent_id, status="working", current_task_id=task_id, last_summary=title, progress=0.15)
    events.emit(
        "agent.started_task",
        project_id=project_id,
        task_id=task_id,
        agent_id=agent_id,
        department_id=dept,
        status="working",
        summary=f"{title}: started",
        progress=0.15,
    )
    search_blob = ""
    if query:
        _set_agent(agent_id, status="researching", current_tool="web_search", progress=0.4)
        events.emit(
            "agent.using_tool",
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            department_id=dept,
            status="researching",
            tool="web_search",
            summary=f"Searching: {query}",
            progress=0.4,
        )
        hits = web_search(query)
        search_blob = json.dumps(hits)[:4000]
        events.emit(
            "agent.researching",
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            department_id=dept,
            status="researching",
            tool="web_search",
            summary=f"Received {len(hits)} search hits",
            progress=0.6,
        )
        time.sleep(0.2)

    text, tokens = complete(
        f"You are {agent_id}, an Ayven specialist. Return useful findings only. No hidden reasoning.",
        f"{brief}\n\nSearch evidence:\n{search_blob}",
    )
    _add_tokens(agent_id, tokens)
    _remember(f"department:{dept}", f"{title}: {text[:500]}")
    _remember(f"project:{project_id}", f"{agent_id}: {text[:800]}")

    conn = connect()
    if needs_approval:
        aid = str(uuid.uuid4())
        conn.execute(
            "UPDATE tasks SET status=?, result=? WHERE id=?",
            ("needs_approval", text, task_id),
        )
        conn.execute(
            """INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (aid, task_id, project_id, agent_id, "Approve draft supplier enquiry before any send.", "pending", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        _set_agent(agent_id, status="needs_approval", current_tool=None, progress=0.8, last_summary="Waiting for Lee")
        events.emit(
            "agent.needs_approval",
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            department_id=dept,
            status="needs_approval",
            summary="Draft outreach ready — Lee must approve before send",
            progress=0.8,
        )
        events.emit(
            "approval.requested",
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            department_id="command",
            status="pending",
            summary="Approve draft supplier enquiry",
        )
        return text

    conn.execute("UPDATE tasks SET status=?, result=? WHERE id=?", ("complete", text, task_id))
    conn.commit()
    conn.close()
    _set_agent(agent_id, status="idle", current_task_id=None, current_tool=None, progress=1, last_summary="Task complete")
    events.emit(
        "agent.completed",
        project_id=project_id,
        task_id=task_id,
        agent_id=agent_id,
        department_id=dept,
        status="idle",
        summary=f"{title}: complete",
        progress=1,
    )
    return text


def resolve_approval(approval_id: str, decision: str) -> None:
    conn = connect()
    row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        raise KeyError("approval not found")
    conn.execute("UPDATE approvals SET status=? WHERE id=?", (decision, approval_id))
    conn.execute(
        "UPDATE tasks SET approval_status=?, status=? WHERE id=?",
        (decision, "complete" if decision == "approved" else "rejected", row["task_id"]),
    )
    conn.commit()
    agent_id = row["agent_id"]
    conn.close()
    _set_agent(agent_id, status="idle", last_summary=f"Approval {decision}", progress=1, current_task_id=None)
    events.emit(
        "approval.resolved",
        project_id=row["project_id"],
        task_id=row["task_id"],
        agent_id=agent_id,
        department_id="command",
        status=decision,
        summary=f"Lee {decision} the outreach draft",
    )


def fail_demo(agent_id: str = "web-researcher") -> None:
    _set_agent(agent_id, status="error", last_summary="Simulated tool timeout")
    events.emit(
        "agent.failed",
        agent_id=agent_id,
        department_id=_dept(agent_id),
        status="error",
        summary="Simulated failure: tool timeout. Retry available.",
    )


def retry_agent(agent_id: str) -> None:
    _set_agent(agent_id, status="idle", last_summary="Recovered after failure")
    events.emit(
        "agent.idle",
        agent_id=agent_id,
        department_id=_dept(agent_id),
        status="idle",
        summary="Agent recovered and is idle",
    )


def _add_tokens(agent_id: str, tokens: int) -> None:
    conn = connect()
    conn.execute(
        "UPDATE agents SET tokens=tokens+?, cost_usd=cost_usd+? WHERE id=?",
        (tokens, tokens / 1000 * COST_PER_1K, agent_id),
    )
    conn.commit()
    conn.close()


def _remember(namespace: str, content: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO memories(id,namespace,content,created_at) VALUES(?,?,?,?)",
        (str(uuid.uuid4()), namespace, content, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
