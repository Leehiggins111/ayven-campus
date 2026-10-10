from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import connect, init_db
from . import events
from . import orchestrator
from .version import VALIDATION_STATUS, __version__

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Ayven Campus API", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
from .access import access_middleware, access_key, protected
app.middleware("http")(access_middleware)
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
    return {
        "ok": True,
        "service": "ayven-api",
        "version": __version__,
        "validation_status": VALIDATION_STATUS,
        "gpu_validated": False,
        "access_protected": protected(),
        "research_mode": __import__("os").environ.get("AYVEN_RESEARCH_MODE", "fixtures"),
        "persistence": "local_sqlite_not_verified_durable",
        "workforce": {"roles": DEFAULT_MODELS, "local_endpoint": local_configured(), "escalation_enabled": escalation_configured()},
    }

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
        "intelligence": _intelligence_state(),
        "campus_brief": _campus_brief(),
        "campus_view": _campus_view(),
    }


def _campus_brief() -> dict:
    rows = _rows(
        "SELECT id, title, stage, status, workflow_state, agent_id, manager_decision, supervisor_decision, "
        "substr(findings,1,400) AS findings, selected_skills, task_class, observability_json "
        "FROM work_packages WHERE parent_id IS NULL ORDER BY updated_at DESC LIMIT 1"
    )
    if not rows:
        return {"doing": "Idle", "why": "No work package is open.", "stuck": False, "needs_you": False, "finished": False, "trust": "nothing in progress"}
    row = rows[0]
    try:
        obs = json.loads(row.get("observability_json") or "{}")
    except json.JSONDecodeError:
        obs = {}
    from .intelligence.observability import summarise

    brief = summarise(obs)
    decision = row.get("manager_decision") or row.get("stage") or "in progress"
    brief.update({
        "doing": decision,
        "package_id": row["id"],
        "stage": row.get("workflow_state") or row.get("stage"),
        "status": row.get("status"),
        "employee": row.get("agent_id"),
        "skills": row.get("selected_skills") or "",
        "task_class": row.get("task_class") or "",
        "manager": row.get("manager_decision") or "",
        "output": row.get("findings") or "",
        "trust": "Evidence and the ledger" if not obs.get("errors") else "Degraded — see the gap list",
        "needs_you": brief["needs_you"] or row.get("status") == "needs_approval" or row.get("workflow_state") in ("AWAITING_APPROVAL", "AWAITING_CLARIFICATION"),
    })
    if row.get("workflow_state") in ("APPROVED", "COMPLETED", "ACTIONING"):
        brief["needs_you"] = False
    if brief["needs_you"] or row.get("workflow_state") in ("AWAITING_APPROVAL", "AWAITING_CLARIFICATION", "REPAIRING", "IN_PROGRESS", "UNDER_REVIEW"):
        brief["finished"] = False
    if row.get("workflow_state") == "COMPLETED":
        brief["finished"] = True
    return brief


def _campus_view(package_id: str = "") -> dict:
    from .intelligence.campus_view import campus_view

    return campus_view(package_id)


def _intelligence_state() -> dict:
    from .intelligence.mcp_boundary import status as mcp_status
    from .intelligence.qwen_adapter import status as qwen_status

    return {
        "version": __version__,
        "validation_status": VALIDATION_STATUS,
        "plans": _rows("SELECT id,package_id,task_class,deliverable,created_at FROM plans ORDER BY created_at DESC LIMIT 20"),
        "skills_used": _rows("SELECT package_id,skill_name,version,reason,created_at FROM skills_used ORDER BY created_at DESC LIMIT 40"),
        "tool_calls": _rows("SELECT id,package_id,agent_id,tool,status,query,source_url,source_title,retrieved_at,error,created_at FROM tool_calls ORDER BY created_at DESC LIMIT 40"),
        "claims": _rows("SELECT id,package_id,agent_id,claim_type,status,confidence,source_url,source_type,freshness,substr(claim_text,1,240) AS claim_text FROM claims ORDER BY created_at DESC LIMIT 40"),
        "verification": _rows("SELECT package_id,stage,agent_id,decision,created_at FROM verification_results ORDER BY created_at DESC LIMIT 40"),
        "quality": _rows("SELECT package_id,overall,created_at FROM quality_results ORDER BY created_at DESC LIMIT 20"),
        "model_calls": _rows("SELECT package_id,role,model_id,backend,prompt_tokens,completion_tokens,latency_s,est_cost_usd,created_at FROM model_calls ORDER BY created_at DESC LIMIT 40"),
        "mcp": mcp_status(),
        "qwen_agent": qwen_status(),
    }


@app.get("/work-packages/{package_id}/deliverable.md")
def download_deliverable(package_id: str):
    """The stored plan or program, the same text Campus shows, as a file."""
    packages = _rows("SELECT id, findings FROM work_packages WHERE id=?", (package_id,))
    if not packages:
        raise HTTPException(404)
    body = packages[0].get("findings") or ""
    filename = f"{package_id[:8]}-deliverable.md"
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/work-packages/{package_id}/files/{name}")
def download_program_file(package_id: str, name: str):
    """A program file whose tests passed. A rejected reply has no file to download."""
    from .intelligence.software import stored_files

    if name not in {"main.py", "test_main.py"}:
        raise HTTPException(404)
    packages = _rows("SELECT id, findings FROM work_packages WHERE id=?", (package_id,))
    if not packages:
        raise HTTPException(404)
    source = stored_files(packages[0].get("findings") or "").get(name)
    if not source:
        raise HTTPException(404)
    return Response(
        content=source,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/work-packages/{package_id}/intelligence")
def package_intelligence(package_id: str):
    packages = _rows("SELECT * FROM work_packages WHERE id=?", (package_id,))
    if not packages:
        raise HTTPException(404)
    return {
        "package": packages[0],
        "plan": _rows("SELECT * FROM plans WHERE package_id=?", (package_id,)),
        "skills_used": _rows("SELECT * FROM skills_used WHERE package_id=?", (package_id,)),
        "tool_calls": _rows("SELECT * FROM tool_calls WHERE package_id=? ORDER BY created_at", (package_id,)),
        "claims": _rows("SELECT * FROM claims WHERE package_id=? ORDER BY created_at", (package_id,)),
        "claim_evidence": _rows("SELECT * FROM claim_evidence WHERE package_id=? ORDER BY created_at", (package_id,)),
        "verification": _rows("SELECT * FROM verification_results WHERE package_id=? ORDER BY created_at", (package_id,)),
        "quality": _rows("SELECT * FROM quality_results WHERE package_id=? ORDER BY created_at", (package_id,)),
        "model_calls": _rows("SELECT * FROM model_calls WHERE package_id=? ORDER BY created_at", (package_id,)),
        "traces": _traces(package_id),
    }


@app.get("/work-packages/{package_id}/traces")
def package_traces(package_id: str):
    from .intelligence.observability import traces_for

    packages = _rows("SELECT id FROM work_packages WHERE id=?", (package_id,))
    if not packages:
        raise HTTPException(404)
    return {"package_id": package_id, "traces": traces_for(package_id)}


def _traces(package_id: str) -> list[dict]:
    from .intelligence.observability import traces_for

    return traces_for(package_id)

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


@app.get("/projects/{pid}/evaluation")
def project_evaluation(pid: str):
    rows = _rows("SELECT * FROM projects WHERE id=?", (pid,))
    if not rows:
        raise HTTPException(404)
    packages = _rows(
        "SELECT id, status, workflow_state, manager_decision, findings, observability_json "
        "FROM work_packages WHERE project_id=? AND parent_id IS NULL ORDER BY updated_at DESC LIMIT 1",
        (pid,),
    )
    if not packages:
        return {"project_id": pid, "status": rows[0]["status"], "evaluation": None}
    package = packages[0]
    try:
        obs = json.loads(package.get("observability_json") or "{}")
    except json.JSONDecodeError:
        obs = {}
    from .intelligence.recovery import final_evaluation

    evaluation = obs.get("evaluation") or final_evaluation(obs, decision=package.get("manager_decision") or "", findings=package.get("findings") or "")
    return {
        "project_id": pid,
        "package_id": package["id"],
        "status": package.get("workflow_state") or package.get("status") or rows[0]["status"],
        "evaluation": evaluation,
    }


@app.get("/campus/view")
def campus_view_route(package_id: str = ""):
    return _campus_view(package_id)

class ClarificationIn(BaseModel):
    answer: str

@app.post("/work-packages/{package_id}/clarification")
def answer_clarification(package_id: str, body: ClarificationIn):
    from .intelligence.execution import begin_clarification, start_clarification_worker

    try:
        begin_clarification(package_id, body.answer)
    except KeyError:
        raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    start_clarification_worker(package_id)
    return {"ok": True, "package_id": package_id}

@app.get("/approvals")
def list_approvals():
    return {"approvals": _rows("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 50")}

@app.post("/approvals/{aid}/resolve")
def resolve(aid: str, body: ApprovalIn):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    try:
        orchestrator.resolve_approval(aid, body.decision)
    except KeyError:
        raise HTTPException(404)
    resumed = []
    if body.decision == "approved":
        from .intelligence.execution import resume_packages_for_approval

        resumed = resume_packages_for_approval(aid)
    return {"ok": True, "resumed": resumed}

@app.post("/demo/fail")
def demo_fail():
    orchestrator.fail_demo()
    return {"ok": True}

@app.post("/demo/retry")
def demo_retry():
    orchestrator.retry_agent("web-researcher")
    return {"ok": True}

class LoginIn(BaseModel):
    key: str


@app.post("/login")
def login(body: LoginIn):
    import hashlib
    from fastapi.responses import JSONResponse
    if not protected():
        return {"ok": True, "protected": False}
    if body.key != access_key():
        raise HTTPException(401, "login required")
    response = JSONResponse({"ok": True, "protected": True})
    response.set_cookie("ayven_session", hashlib.sha256(access_key().encode()).hexdigest(), httponly=True, samesite="lax")
    return response


@app.post("/milo/jobs")
def milo_submit(body: ObjectiveIn, idempotency_key: str = ""):
    from .milo_handoff import submit_job
    return submit_job(body.objective, idempotency_key)


@app.get("/milo/jobs/{project_id}")
def milo_status(project_id: str):
    from .milo_handoff import job_status
    try:
        return job_status(project_id)
    except KeyError:
        raise HTTPException(404)


@app.get("/admin/backup")
def backup():
    import hashlib
    import shutil
    from .db import db_path
    target = Path("/tmp/ayven-backup.db")
    shutil.copy(db_path(), target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"ok": True, "path": str(target), "sha256": digest, "durable": False, "note": "Local copy only. No free remote store is connected."}


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
