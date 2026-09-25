from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import connect, init_db
from . import events
from . import orchestrator

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Ayven Campus API", version="0.4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/r3f", StaticFiles(directory=str(STATIC / "r3f")), name="r3f")

def _campus_index() -> Path:
    r3f = STATIC / "r3f" / "index.html"
    return r3f if r3f.exists() else STATIC / "campus.html"

@app.get("/")
def campus_root():
    return FileResponse(_campus_index())

@app.get("/campus")
def campus_page():
    return FileResponse(_campus_index())

@app.get("/legacy")
def campus_legacy():
    return FileResponse(STATIC / "campus.html")

@app.on_event("startup")
def startup() -> None:
    conn = connect()
    init_db(conn)
    conn.close()

class ObjectiveIn(BaseModel):
    objective: str
    title: str | None = None

class ApprovalIn(BaseModel):
    decision: str

def _rows(sql: str, args=()):
    conn = connect()
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows

@app.get("/health")
def health():
    from .models import DEFAULT_MODELS, escalation_configured, local_configured
    return {"ok": True, "service": "ayven-api", "version": "0.4.0", "workforce": {"roles": DEFAULT_MODELS, "local_endpoint": local_configured(), "escalation_enabled": escalation_configured()}}

@app.get("/state")
def state():
    return {
        "departments": _rows("SELECT * FROM departments"),
        "agents": _rows("SELECT * FROM agents"),
        "projects": _rows("SELECT * FROM projects ORDER BY created_at DESC LIMIT 20"),
        "tasks": _rows("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50"),
        "approvals": _rows("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 20"),
        "events": _rows("SELECT * FROM events ORDER BY timestamp DESC LIMIT 80"),
        "memories": _rows("SELECT id,namespace,created_at FROM memories ORDER BY created_at DESC LIMIT 20"),
        "work_packages": _rows("SELECT * FROM work_packages ORDER BY updated_at DESC LIMIT 20"),
        "sources": _rows("SELECT * FROM sources ORDER BY created_at DESC LIMIT 40"),
    }

@app.post("/projects")
def create_project(body: ObjectiveIn):
    pid = str(uuid.uuid4())
    title = body.title or body.objective[:80]
    conn = connect()
    conn.execute("INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)", (pid, title, body.objective, "running", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    events.emit("project.created", project_id=pid, agent_id="milo", department_id="command", status="running", summary=f"Project opened: {title}")
    orchestrator.run_project(pid)
    return {"project_id": pid}

@app.get("/projects/{pid}")
def get_project(pid: str):
    rows = _rows("SELECT * FROM projects WHERE id=?", (pid,))
    if not rows:
        raise HTTPException(404)
    return {**rows[0], "tasks": _rows("SELECT * FROM tasks WHERE project_id=?", (pid,)), "work_packages": _rows("SELECT * FROM work_packages WHERE project_id=?", (pid,))}

@app.post("/approvals/{aid}/resolve")
def resolve(aid: str, body: ApprovalIn):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    try:
        orchestrator.resolve_approval(aid, body.decision)
    except KeyError:
        raise HTTPException(404)
    return {"ok": True}

@app.post("/demo/fail")
def demo_fail():
    orchestrator.fail_demo()
    return {"ok": True}

@app.post("/demo/retry")
def demo_retry():
    orchestrator.retry_agent("web-researcher")
    return {"ok": True}

@app.get("/events/stream")
def stream():
    q = events.subscribe()
    def gen():
        try:
            yield "retry: 2000\n"
            yield ": " + ("pad" * 400) + "\n\n"
            yield "data: {\"type\":\"stream.hello\"}\n\n"
            while True:
                try:
                    ev = q.get(timeout=12)
                    yield f"data: {json.dumps(ev)}\n\n"
                except Exception:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
