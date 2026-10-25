"""Ayven OS application/service layer.

Ayven OS is the platform; Milo is one assistant inside it. Every major operation should be reachable the same way
from the graphical interface and from Milo, with the same permissions and the same audit record. This module is
that shared layer: a registry of named ACTIONS, each mapping to a real existing Campus capability, invoked through
one documented interface:

    GET  /os/actions            -> the action contract both the UI and Milo read (ids, schemas, example payloads)
    GET  /os/actions/{id}       -> one action's contract
    POST /os/actions/{id}       -> invoke it (owner auth; input validated; result returned)
    GET  /os/audit              -> the durable audit trail of every invocation

Guarantees:
* Authorisation. The Campus access boundary already gates every /os route to the owner (bearer token or signed-in
  session). A read-only token (AYVEN_READ_TOKEN) can call GET but not POST, so it can read the contract and run
  read actions via... no: invocation is POST, so a read-only token cannot invoke anything. Each action also
  declares `mutates`; a mutating action additionally refuses a read-only principal.
* Persistence. Actions read and write the real Campus SQLite database (the same `agents`, `projects`, ... tables
  the rest of the platform uses), so an agent created here appears in `/state` and the 3D view.
* Audit. Every invocation — allowed or refused, succeeded or failed — is written to `os_audit` before the result is
  returned, with who did it, the action, a hash and short summary of the input, and the outcome.
* It never bypasses a disabled control. It exposes existing capabilities only; it does not enable owner approvals,
  unsandboxed execution, or anything the platform has switched off. Mutating actions here are limited to the agent
  registry and OS-native records, never code execution or deployment.

The same registry is what Milo proxies (see Milo `src/server/campus/os.js`), so the assistant and the interface
invoke identical, identically-audited operations.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from .db import connect

router = APIRouter()
SCHEMA_VERSION = "ayven.os/v1"
NAME_RE = re.compile(r"^[\w .,'()\-/&]{1,80}$")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


# ---------------------------------------------------------------- storage
def ensure_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS os_audit (
        id TEXT PRIMARY KEY, ts INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        mutates INTEGER NOT NULL, input_sha256 TEXT NOT NULL, input_summary TEXT NOT NULL,
        outcome TEXT NOT NULL, detail TEXT NOT NULL)""")
    # OS-managed agents live in the platform's own `agents` table. Add provenance columns the OS layer fills;
    # they are nullable so the seeded agents and every existing reader (/state) are untouched.
    cols = {r["name"] for r in c.execute("PRAGMA table_info(agents)").fetchall()}
    for name in ("created_at", "created_by", "managed_by"):
        if name not in cols:
            c.execute(f"ALTER TABLE agents ADD COLUMN {name} TEXT")


def audit(c, actor, action, mutates, payload, outcome, detail=""):
    raw = json.dumps(payload, sort_keys=True, default=str).encode()
    summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in sorted((payload or {}).items()))[:300]
    row = (uuid.uuid4().hex, int(time.time()), str(actor), action, int(bool(mutates)),
           hashlib.sha256(raw).hexdigest(), summary, outcome, str(detail)[:1000])
    c.execute("INSERT INTO os_audit VALUES(?,?,?,?,?,?,?,?,?)", row)
    return row[0]


# ---------------------------------------------------------------- identity
def actor_of(request: Request) -> str:
    """Who is invoking, as far as Campus can tell. Milo passes the signed-in owner in X-Ayven-Owner over its
    bearer-authenticated call; a direct bearer/session is the owner. Recorded, never trusted for authorisation
    (the access boundary already decided that)."""
    who = (request.headers.get("X-Ayven-Owner") or "").strip()[:200]
    if who:
        return who
    if request.headers.get("authorization", "").startswith("Bearer "):
        return "owner:bearer"
    if request.cookies.get("ayven_session"):
        return "owner:session"
    return "owner:local"


def is_read_only(request: Request) -> bool:
    import os
    read_key = os.environ.get("AYVEN_READ_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    return bool(read_key) and supplied == "Bearer " + read_key


# ---------------------------------------------------------------- validation helpers
def _str(data, field, *, required=True, maxlen=4000, pattern=None, default=""):
    v = data.get(field, default)
    if v in ("", None):
        if required:
            raise HTTPException(400, f"{field} is required")
        return default
    if not isinstance(v, str) or len(v) > maxlen:
        raise HTTPException(400, f"{field} must be a string of at most {maxlen} characters")
    if pattern and not pattern.match(v):
        raise HTTPException(400, f"{field} is not in the expected format")
    return v


def _int(data, field, *, lo, hi, default=None):
    v = data.get(field, default)
    if v is None:
        return default
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise HTTPException(400, f"{field} must be an integer in [{lo},{hi}]")
    return v


# ---------------------------------------------------------------- handlers (each returns JSON-able data)
def _agent_view(r):
    return {k: r[k] for k in ("id", "name", "role", "department_id", "status", "current_task_id", "progress",
                              "tokens", "cost_usd", "created_at", "created_by", "managed_by") if k in r.keys()}


def h_agent_list(c, data, request):
    dept = data.get("department_id")
    if dept is not None and not isinstance(dept, str):
        raise HTTPException(400, "department_id must be a string")
    rows = c.execute("SELECT * FROM agents" + (" WHERE department_id=?" if dept else "") + " ORDER BY name",
                     ((dept,) if dept else ())).fetchall()
    return {"agents": [_agent_view(r) for r in rows]}


def h_agent_get(c, data, request):
    r = c.execute("SELECT * FROM agents WHERE id=?", (_str(data, "id", maxlen=64),)).fetchone()
    if not r:
        raise HTTPException(404, "No such agent")
    return {"agent": _agent_view(r)}


def h_agent_create(c, data, request):
    name = _str(data, "name", pattern=NAME_RE)
    role = _str(data, "role", pattern=NAME_RE)
    dept = _str(data, "department_id", maxlen=64)
    if not c.execute("SELECT 1 FROM departments WHERE id=?", (dept,)).fetchone():
        raise HTTPException(400, f"Unknown department_id {dept!r}; create it first or use an existing one")
    agent_id = "ag-" + uuid.uuid4().hex[:12]
    c.execute("INSERT INTO agents(id,name,role,department_id,status,progress,tokens,cost_usd,created_at,"
              "created_by,managed_by) VALUES(?,?,?,?,'idle',0,0,0,?,?, 'ayven-os')",
              (agent_id, name, role, dept, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), actor_of(request)))
    return {"agent": _agent_view(c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())}


def h_agent_retire(c, data, request):
    agent_id = _str(data, "id", maxlen=64)
    r = c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not r:
        raise HTTPException(404, "No such agent")
    if r["status"] not in ("idle", "retired"):
        raise HTTPException(409, f"Agent is {r['status']}; it can be retired only when idle")
    if r["current_task_id"]:
        raise HTTPException(409, "Agent is assigned to a task; reassign or finish it first")
    c.execute("UPDATE agents SET status='retired' WHERE id=?", (agent_id,))
    return {"agent": _agent_view(c.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())}


def h_department_list(c, data, request):
    return {"departments": [dict(r) for r in c.execute("SELECT id,name FROM departments ORDER BY name").fetchall()]}


def h_project_list(c, data, request):
    limit = _int(data, "limit", lo=1, hi=200, default=50)
    rows = c.execute("SELECT id,title,objective,status,created_at FROM projects ORDER BY created_at DESC LIMIT ?",
                     (limit,)).fetchall()
    return {"projects": [dict(r) for r in rows]}


def h_project_get(c, data, request):
    pid = _str(data, "id", maxlen=64)
    r = c.execute("SELECT id,title,objective,status,result,created_at FROM projects WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "No such project")
    tasks = c.execute("SELECT id,title,status,requires_approval,approval_status FROM tasks WHERE project_id=?",
                      (pid,)).fetchall()
    return {"project": dict(r), "tasks": [dict(t) for t in tasks]}


def h_execution_list(c, data, request):
    from .workbench_bridge import ensure_tables as wb_tables
    wb_tables(c)
    limit = _int(data, "limit", lo=1, hi=100, default=25)
    rows = c.execute("SELECT id,project_id,objective,state,attempts,created_at FROM workbench_orders "
                     "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return {"executions": [dict(r) for r in rows]}


# ---------------------------------------------------------------- the registry
class Action:
    def __init__(self, id, title, category, mutates, handler, schema, example):
        self.id, self.title, self.category = id, title, category
        self.mutates, self.handler, self.schema, self.example = mutates, handler, schema, example

    def contract(self):
        return {"id": self.id, "title": self.title, "category": self.category, "mutates": self.mutates,
                "auth": "owner", "method": "POST", "path": f"/os/actions/{self.id}",
                "input_schema": self.schema, "example_input": self.example}


ACTIONS: dict[str, Action] = {}


def _register(*args):
    a = Action(*args)
    ACTIONS[a.id] = a


_register("agent.list", "List agents", "agents", False, h_agent_list,
          {"department_id": "string (optional filter)"}, {})
_register("agent.get", "Get an agent", "agents", False, h_agent_get,
          {"id": "string (required)"}, {"id": "ag-000000000000"})
_register("agent.create", "Create an agent", "agents", True, h_agent_create,
          {"name": "string (required)", "role": "string (required)", "department_id": "string (required)"},
          {"name": "Grace Researcher", "role": "Research analyst", "department_id": "research"})
_register("agent.retire", "Retire an idle agent", "agents", True, h_agent_retire,
          {"id": "string (required)"}, {"id": "ag-000000000000"})
_register("department.list", "List departments", "agents", False, h_department_list, {}, {})
_register("project.list", "List projects", "projects", False, h_project_list,
          {"limit": "integer 1-200 (optional, default 50)"}, {"limit": 20})
_register("project.get", "Get a project and its tasks", "projects", False, h_project_get,
          {"id": "string (required)"}, {"id": "proj-..."})
_register("execution.list", "List Workbench coding executions", "executions", False, h_execution_list,
          {"limit": "integer 1-100 (optional, default 25)"}, {"limit": 25})


def manifest():
    return {"schema": SCHEMA_VERSION, "actions": [a.contract() for a in ACTIONS.values()]}


# ---------------------------------------------------------------- routes
@router.get("/os/actions")
def list_actions():
    return manifest()


@router.get("/os/actions/{action_id}")
def get_action(action_id: str):
    a = ACTIONS.get(action_id)
    if not a:
        raise HTTPException(404, "No such action")
    return a.contract()


@router.post("/os/actions/{action_id}")
async def invoke_action(action_id: str, request: Request):
    a = ACTIONS.get(action_id)
    try:
        raw = await request.body()
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        data = None
    c = connect()
    try:
        ensure_tables(c)
        actor = actor_of(request)
        if a is None:
            audit(c, actor, action_id, False, {}, "not_found")
            c.commit()
            raise HTTPException(404, "No such action")
        if data is None:
            audit(c, actor, a.id, a.mutates, {}, "bad_request", "body must be a JSON object")
            c.commit()
            raise HTTPException(400, "Request body must be a JSON object")
        if a.mutates and is_read_only(request):
            audit(c, actor, a.id, True, data, "forbidden", "read-only token")
            c.commit()
            raise HTTPException(403, "This action changes data; a read-only token cannot invoke it")
        try:
            result = a.handler(c, data, request)
        except HTTPException as exc:
            audit(c, actor, a.id, a.mutates, data, "refused", f"{exc.status_code}: {exc.detail}")
            c.commit()
            raise
        audit_id = audit(c, actor, a.id, a.mutates, data, "ok")
        c.commit()
        return {"action": a.id, "ok": True, "audit_id": audit_id, "result": result}
    finally:
        c.close()


@router.get("/os/audit")
def os_audit(limit: int = 100, action: str = ""):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be between 1 and 500")
    c = connect()
    try:
        ensure_tables(c)
        sql = "SELECT id,ts,actor,action,mutates,input_sha256,input_summary,outcome,detail FROM os_audit"
        args: tuple = ()
        if action:
            sql += " WHERE action=?"
            args = (action,)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        rows = c.execute(sql, (*args, limit)).fetchall()
        return {"entries": [dict(r) for r in rows]}
    finally:
        c.close()
