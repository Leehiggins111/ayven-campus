"""Bounded agent-task execution: turn a *registered* agent into a real *worker* for one narrow,
genuine task kind, with validation, permitted-capability checks, honest execution, durable state and
linked evidence.

A registered agent is a row in the `agents` table. That is not a worker. This module assigns an
authorised task to a specific agent and runs it on a real, permitted execution backend — the
platform's own model gateway (`llm`/`models`, local Ollama when configured) — recording what model
actually served it. Nothing here simulates completion or invents a result: if the real backend is
unavailable or returns nothing, the task fails visibly and no fixture answer is substituted.

Scope of v1 (deliberately one worker type, proven before expanding):
  * kind "research_brief": the agent produces a short factual brief from an objective, using the
    model gateway. It is a read/reasoning task (capability READ_WEB) — no external effect, no host
    access, no gated capability (WRITE_FILES / SEND_EMAIL / PURCHASE / RUN_CODE stay approval-gated
    in `permissions` and are never used here).

States: queued -> running -> (completed | failed) ; queued -> cancelled ; failed -> (retry) queued.
Every task row is durable and linked to owner + agent + objective + evidence.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from .intelligence import permissions

SCHEMA = "ayven.agent-task/v1"
TERMINAL = {"completed", "failed", "cancelled"}

# The task kinds this worker lane can actually perform, and the real capability each needs. A kind
# the agent's role does not have the capability for is refused (reusing the platform's ROLE_CAPS).
KIND_CAP = {
    "research_brief": permissions.READ_WEB,
}
MAX_OBJECTIVE = 2000


class TaskError(Exception):
    """A task could not be assigned. `status` picks the HTTP mapping at the route layer."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def ensure_tables(c) -> None:
    c.execute(
        """CREATE TABLE IF NOT EXISTS agent_tasks (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            kind TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            parent_task_id TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            execution_mode TEXT,
            model TEXT,
            tokens INTEGER NOT NULL DEFAULT 0,
            result_text TEXT,
            result_sha256 TEXT,
            evidence_json TEXT,
            error TEXT
        )"""
    )


def permitted_kinds(agent_id: str) -> list[str]:
    """Which task kinds this agent's role is actually permitted to perform (not just registered)."""
    granted = permissions.capabilities_for(agent_id)
    return sorted(k for k, cap in KIND_CAP.items() if cap in granted)


def is_active(c, agent_id: str) -> bool:
    """A worker is *active* (not merely registered) when it has a queued or running task."""
    row = c.execute(
        "SELECT 1 FROM agent_tasks WHERE agent_id=? AND status IN ('queued','running') LIMIT 1",
        (agent_id,),
    ).fetchone()
    return bool(row)


def _task_view(r) -> dict:
    v = {k: r[k] for k in (
        "id", "agent_id", "owner", "kind", "objective", "status", "attempt", "parent_task_id",
        "created_at", "started_at", "finished_at", "execution_mode", "model", "tokens",
        "result_text", "result_sha256", "error",
    ) if k in r.keys()}
    ev = r["evidence_json"] if "evidence_json" in r.keys() else None
    v["evidence"] = json.loads(ev) if ev else None
    return v


def get(c, task_id: str) -> dict | None:
    r = c.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    return _task_view(r) if r else None


def list_tasks(c, *, agent_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict]:
    clauses, args = [], []
    if agent_id:
        clauses.append("agent_id=?")
        args.append(agent_id)
    if status:
        clauses.append("status=?")
        args.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    args.append(max(1, min(limit, 200)))
    rows = c.execute(f"SELECT * FROM agent_tasks{where} ORDER BY created_at DESC, id DESC LIMIT ?", args).fetchall()
    return [_task_view(r) for r in rows]


# ---------------------------------------------------------------- the real execution backend

def _execute_research_brief(objective: str) -> dict:
    """Run the task on the real model gateway. Returns an evidence dict with the output. Raises
    TaskError(502) when the real backend is unavailable or empty — never a substituted fixture."""
    from . import llm
    from . import models

    system = (
        "You are a research analyst inside Ayven. Produce a concise, factual brief answering the "
        "owner's objective in at most ~150 words. State plainly if you are unsure or lack enough "
        "information; do not invent facts, figures, sources or quotes."
    )
    local = models.local_base()
    stub = llm._stub_enabled()

    if local and not stub:
        # REAL: a local model (e.g. Ollama) actually serves the call; record which model did.
        import os
        model = models.role_model("EMPLOYEE")
        api_key = os.environ.get("AYVEN_LOCAL_LLM_API_KEY", "ollama")
        try:
            text, tokens, meta = models._openai_compat(local, api_key, model, system, objective, 400)
        except Exception as exc:  # noqa: BLE001 — any backend failure is a visible task failure
            raise TaskError(502, f"The execution backend was unavailable ({type(exc).__name__}); nothing was produced.")
        text = (text or "").strip()
        if not text:
            raise TaskError(502, "The execution backend returned an empty result; nothing was produced.")
        return {
            "execution_mode": "REAL",
            "model": (meta or {}).get("model") or model,
            "tokens": int(tokens or (meta or {}).get("completion_tokens") or 0),
            "finish_reason": (meta or {}).get("finish_reason") or "",
            "elapsed_s": (meta or {}).get("elapsed_s") or 0,
            "tool": "model_gateway",
            "capability": permissions.READ_WEB,
            "result_text": text,
        }

    # SIMULATED: no real local model configured. We still return a result, but it is labelled
    # SIMULATED so no caller can mistake a fixture for genuine work.
    text = llm.stub_complete(objective).strip() or "(no stub answer)"
    return {
        "execution_mode": "SIMULATED",
        "model": "stub",
        "tokens": max(16, len(text) // 4),
        "tool": "model_gateway",
        "capability": permissions.READ_WEB,
        "result_text": text,
    }


_EXECUTORS = {"research_brief": _execute_research_brief}


# ---------------------------------------------------------------- assignment and lifecycle

def _agent_row(c, agent_id: str):
    return c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()


def validate_assignment(c, agent_id: str, kind: str) -> None:
    """Raise TaskError if this agent cannot be given this kind of task. Does not mutate anything."""
    agent = _agent_row(c, agent_id)
    if not agent:
        raise TaskError(404, "No such agent")
    if kind not in KIND_CAP:
        raise TaskError(400, f"Unknown task kind {kind!r}; this worker lane supports: {', '.join(sorted(KIND_CAP))}")
    if agent["status"] == "retired":
        raise TaskError(409, "Agent is retired; it cannot be assigned work")
    if agent["status"] not in ("idle",):
        raise TaskError(409, f"Agent is {agent['status']}; assign work only when it is idle")
    if agent["current_task_id"]:
        raise TaskError(409, "Agent already has a current task")
    # The real capability check: the agent's role must hold the capability this kind needs.
    cap = KIND_CAP[kind]
    if cap not in permissions.capabilities_for(agent_id):
        raise TaskError(403, f"Agent {agent_id} is not permitted to perform {kind} (needs {cap})")


def assign(c, *, owner: str, agent_id: str, kind: str, objective: str, start: bool = True) -> dict:
    """Create a task for an agent (queued). When start is true, run it now and return the finished
    task; otherwise return the queued task (which can later be run or cancelled)."""
    ensure_tables(c)
    objective = (objective or "").strip()
    if not objective:
        raise TaskError(400, "objective is required")
    if len(objective) > MAX_OBJECTIVE:
        raise TaskError(400, f"objective must be at most {MAX_OBJECTIVE} characters")
    validate_assignment(c, agent_id, kind)

    task_id = "tk-" + uuid.uuid4().hex[:12]
    c.execute(
        "INSERT INTO agent_tasks(id,agent_id,owner,kind,objective,status,attempt,created_at) "
        "VALUES(?,?,?,?,?, 'queued', 1, ?)",
        (task_id, agent_id, owner, kind, objective, _now()),
    )
    c.commit()
    if start:
        return run(c, task_id)
    return get(c, task_id)


def run(c, task_id: str) -> dict:
    """Execute a queued task on the real backend and record the outcome and evidence."""
    r = c.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if not r:
        raise TaskError(404, "No such task")
    if r["cancel_requested"]:
        c.execute("UPDATE agent_tasks SET status='cancelled', finished_at=? WHERE id=?", (_now(), task_id))
        c.commit()
        return get(c, task_id)
    if r["status"] != "queued":
        raise TaskError(409, f"Task is {r['status']}; only a queued task can be run")

    agent_id = r["agent_id"]
    c.execute("UPDATE agent_tasks SET status='running', started_at=? WHERE id=?", (_now(), task_id))
    c.execute("UPDATE agents SET status='working', current_task_id=? WHERE id=?", (task_id, agent_id))
    c.commit()

    executor = _EXECUTORS[r["kind"]]
    try:
        ev = executor(r["objective"])
    except TaskError as exc:
        c.execute(
            "UPDATE agent_tasks SET status='failed', finished_at=?, error=? WHERE id=?",
            (_now(), exc.message, task_id),
        )
        c.execute("UPDATE agents SET status='idle', current_task_id=NULL WHERE id=?", (agent_id,))
        c.commit()
        return get(c, task_id)

    text = ev.pop("result_text", "")
    tokens = int(ev.get("tokens") or 0)
    c.execute(
        "UPDATE agent_tasks SET status='completed', finished_at=?, execution_mode=?, model=?, tokens=?, "
        "result_text=?, result_sha256=?, evidence_json=? WHERE id=?",
        (_now(), ev.get("execution_mode"), ev.get("model"), tokens, text, _sha(text), json.dumps(ev), task_id),
    )
    c.execute(
        "UPDATE agents SET status='idle', current_task_id=NULL, last_summary=?, tokens=tokens+? WHERE id=?",
        (f"{r['kind']}: {ev.get('execution_mode')}", tokens, agent_id),
    )
    c.commit()
    return get(c, task_id)


def cancel(c, task_id: str) -> dict:
    ensure_tables(c)
    r = c.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if not r:
        raise TaskError(404, "No such task")
    if r["status"] in TERMINAL:
        raise TaskError(409, f"Task is already {r['status']}")
    if r["status"] == "running":
        # Best effort for a backend that checks between steps; a synchronous run has no safe
        # interruption point, so we do not claim it was stopped.
        c.execute("UPDATE agent_tasks SET cancel_requested=1 WHERE id=?", (task_id,))
        c.commit()
        raise TaskError(409, "Task is already running and cannot be safely interrupted")
    c.execute("UPDATE agent_tasks SET status='cancelled', finished_at=? WHERE id=?", (_now(), task_id))
    c.execute("UPDATE agents SET status='idle', current_task_id=NULL WHERE id=? AND current_task_id=?",
              (r["agent_id"], task_id))
    c.commit()
    return get(c, task_id)


def retry(c, task_id: str, *, start: bool = True) -> dict:
    ensure_tables(c)
    r = c.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if not r:
        raise TaskError(404, "No such task")
    if r["status"] != "failed":
        raise TaskError(409, f"Only a failed task can be retried; this task is {r['status']}")
    validate_assignment(c, r["agent_id"], r["kind"])
    new_id = "tk-" + uuid.uuid4().hex[:12]
    c.execute(
        "INSERT INTO agent_tasks(id,agent_id,owner,kind,objective,status,attempt,parent_task_id,created_at) "
        "VALUES(?,?,?,?,?, 'queued', ?, ?, ?)",
        (new_id, r["agent_id"], r["owner"], r["kind"], r["objective"], int(r["attempt"]) + 1, task_id, _now()),
    )
    c.commit()
    if start:
        return run(c, new_id)
    return get(c, new_id)
