from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from .db import connect, init_db, persistence_report
from . import events
from . import orchestrator
from .version import VALIDATION_STATUS, __version__

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Ayven Campus API", version=__version__)
from .access import boundary, login, create_session, validate_public_configuration, protected, access_key, session_value, TTL
app.middleware("http")(boundary)
app.add_api_route("/login", login, methods=["GET"])
app.add_api_route("/session", create_session, methods=["POST"])
origins = [x.strip() for x in os.environ.get("AYVEN_CORS_ORIGINS", "").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
from .milo_bridge import router as milo_router
app.include_router(milo_router)
from .workbench_bridge import router as workbench_router
app.include_router(workbench_router)
from .ayven_os import router as ayven_os_router
app.include_router(ayven_os_router)
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
async def startup() -> None:
    validate_public_configuration()
    conn = connect()
    init_db(conn)
    conn.close()
    from .intelligence.recovery import start_recovery

    thread, done = start_recovery()
    app.state.recovery_thread = thread
    app.state.recovery_done = done

@app.on_event("shutdown")
async def shutdown() -> None:
    from .durable import shutdown as stop_worker
    await run_in_threadpool(stop_worker)
    from .intelligence.web_preview import close_previews
    await run_in_threadpool(close_previews)

class ObjectiveIn(BaseModel):
    objective: str
    title: str | None = None
    acceptance_criteria: list[str] = []
    constraints: list[str] | str = []
    deadline: str | None = None
    job_cap_gbp: float | None = None
    budget_usd: float | None = None
    allow_paid_fallback: bool = False
    allow_publish: bool = False

class ApprovalIn(BaseModel):
    decision: str
    reason: str = ""
    # Approving a result with an unmet acceptance criterion is refused unless Lee
    # says so explicitly, with a reason. The override is recorded on the approval.
    accept_unmet_criteria: bool = False


class AcceptIn(BaseModel):
    accept_unmet_criteria: bool = False
    reason: str = ""

class BudgetIn(BaseModel):
    monthly_cap_gbp: float

class CapIn(BaseModel):
    job_cap_gbp: float

class ReworkIn(BaseModel):
    reason: str

def _rows(sql: str, args=()):
    conn = connect()
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows

@app.get("/health")
def health():
    from .models import DEFAULT_MODELS, escalation_configured, local_configured
    from .durable import enabled as durable_enabled

    # One durability answer: the job runner and the database must both be durable.
    try:
        runtime_durable = durable_enabled()
    except RuntimeError:
        runtime_durable = False
    storage = persistence_report()
    durability = {
        **storage,
        "durable": bool(storage["durable"] and runtime_durable),
        "durable_runtime": runtime_durable,
        "durable_storage": bool(storage["durable"]),
    }
    if not durability["durable"]:
        durability["persistence_state"] = "not_durable"
    # The job engine's own tables (DBOS) stay on this instance's disk unless a workflow
    # database is configured. Jobs restart from checkpoints in the app database, but an
    # instance-local workflow store is reported here, not hidden behind "durable".
    from .durable import _workflow_database_url

    workflow_url = _workflow_database_url()
    workflow_local = workflow_url.startswith("sqlite:")
    durability["workflow_store"] = "instance_local_sqlite" if workflow_local else "configured"
    durability["workflow_store_durable"] = not workflow_local
    durability["durability_caveats"] = (
        ["Workflow engine tables are on instance-local disk. A new instance resumes jobs from app-database checkpoints; this is not yet proven on the hosted service."]
        if workflow_local else []
    )
    if storage["durable"]:
        # Config alone is not proof: a durable database that does not answer is degraded.
        from . import db as _db

        live, error = _db.live_check(timeout=2.0)
        durability["persistence_live"] = live
        if not live:
            durability.update({"durable": False, "durable_storage": False, "persistence_state": "degraded", "persistence_error": error})
    degraded = durability["persistence_state"] == "degraded"
    return {
        # Degraded storage is reported in the body but stays HTTP 200, so a brief
        # database blip does not make Render restart the service or fail a deploy.
        # Monitors that need a failing status use /health/ready.
        "ok": not degraded,
        "service": "ayven-api",
        "version": __version__,
        "validation_status": VALIDATION_STATUS,
        "gpu_validated": False,
        "runtime": "degraded" if degraded else ("durable" if runtime_durable else "thread_stub"),
        "access_protected": protected(),
        "research_mode": os.environ.get("AYVEN_RESEARCH_MODE", "fixtures"),
        **durability,
        "model_mode": "fixture" if os.environ.get("AYVEN_LLM_STUB", "1") != "0" and not local_configured() else "configured",
        "simulated": os.environ.get("AYVEN_LLM_STUB", "1") != "0",
        "label": "SIMULATED" if os.environ.get("AYVEN_LLM_STUB", "1") != "0" else "LIVE",
        "workforce": {"roles": DEFAULT_MODELS, "local_endpoint": local_configured(), "escalation_enabled": escalation_configured()},
    }

@app.get("/health/ready")
def health_ready():
    """Readiness for monitors: HTTP 503 while durable storage is degraded, else 200."""
    from fastapi.responses import JSONResponse

    body = health()
    ready = body["persistence_state"] != "degraded"
    payload = {
        "ready": ready,
        "ok": body["ok"],
        "runtime": body["runtime"],
        "durable": body["durable"],
        "persistence": body["persistence"],
        "persistence_state": body["persistence_state"],
    }
    if not ready:
        payload["persistence_error"] = body.get("persistence_error", "")
    return JSONResponse(payload, status_code=200 if ready else 503)


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
    from .blobs import list_files
    from .goals import criteria_for, get_goal, list_blockers
    from .intelligence.campus_view import campus_view

    view = campus_view(package_id)
    pid = view.get("package_id") or package_id
    project_id = ""
    if pid:
        rows = _rows("SELECT project_id FROM work_packages WHERE id=?", (pid,))
        project_id = rows[0]["project_id"] if rows else ""
    goal = get_goal(project_id) if project_id else None
    view["blockers"] = list_blockers(package_id=pid) if pid else []
    view["files"] = list_files(pid) if pid else []
    view["criteria_results"] = criteria_for(project_id) if project_id else []
    from .goals import criteria_evidence
    from .outcome import outcome_for

    view["criteria_evidence"] = criteria_evidence(project_id) if project_id else []
    view["outcome"] = outcome_for(project_id=project_id, package_id=pid) if (project_id or pid) else None
    if goal:
        view["cost_gbp"] = goal["spent_gbp"]
        view["job_cap_gbp"] = goal["job_cap_gbp"]
        view["allow_paid_fallback"] = goal["allow_paid_fallback"]
        view["allow_publish"] = goal["allow_publish"]
    return view


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
    """The stored plan or program, the same text Campus shows, as a file.

    Only an accepted result downloads unlabelled. A draft, a rejected or failed
    result, or a simulated one starts with a banner saying what it is, and its
    file name says so too. Rejected work is never served as a deliverable.
    """
    from .outcome import banner, download_name, headers, outcome_for

    packages = _rows("SELECT id, findings FROM work_packages WHERE id=?", (package_id,))
    if not packages:
        raise HTTPException(404)
    result = outcome_for(package_id=package_id)
    body = banner(result) + (packages[0].get("findings") or "")
    filename = download_name(package_id[:8], result, f"{package_id[:8]}-deliverable", "md")
    if result["state"] in ("REJECTED", "FAILED", "BLOCKED"):
        filename = download_name(package_id[:8], result, f"{package_id[:8]}-diagnostic", "md")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', **headers(result)},
    )


@app.get("/work-packages/{package_id}/files/{name}")
def download_program_file(package_id: str, name: str):
    """A program file whose tests passed. A rejected reply has no file to download."""
    from .intelligence.software import stored_files
    from .outcome import headers, outcome_for

    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", name):
        raise HTTPException(404)
    packages = _rows("SELECT id, findings FROM work_packages WHERE id=?", (package_id,))
    if not packages:
        raise HTTPException(404)
    result = outcome_for(package_id=package_id)
    if result["state"] not in ("COMPLETED", "NEEDS_APPROVAL"):
        # Source from a rejected, failed, blocked or unfinished job is not handed out as a deliverable.
        raise HTTPException(409, f"This job is {result['state']}. Its files are not a deliverable. {result['next_action']}")
    source = stored_files(packages[0].get("findings") or "").get(name)
    if not source:
        raise HTTPException(404)
    return Response(
        content=source,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', **headers(result)},
    )


def _verified_web_package(package_id: str):
    from .intelligence.web_artifact import artifact_files, verified_web
    rows = _rows("SELECT findings,observability_json FROM work_packages WHERE id=?", (package_id,))
    if not rows:
        raise HTTPException(404, "Package not found")
    row = rows[0]
    try:
        research = json.loads(row.get("research_json") or "{}")
        obs = json.loads(row.get("observability_json") or "{}")
        research["web_execution"] = research.get("web_execution") or obs.get("web_execution") or {}
        research["web_plan"] = research.get("web_plan") or research["web_execution"].get("plan") or {}
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(409, "Browser verification evidence is unavailable")
    if not verified_web(row.get("findings") or "", research):
        raise HTTPException(409, "This browser artifact has not passed verification")
    return artifact_files(row["findings"]), research


@app.get("/work-packages/{package_id}/artifact.zip")
def download_web_artifact(package_id: str):
    import base64
    import io
    import zipfile
    files, research = _verified_web_package(package_id)
    evidence = research["web_execution"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("README.txt", "Offline browser application. No dependencies or external services.\nOpen the preview from Milo to use stable browser storage, or serve this folder on a local HTTP server.\nData belongs to this browser profile and origin. Clearing browser storage deletes it.\nNothing was sent.\n")
        archive.writestr("acceptance-plan.json", json.dumps(research.get("web_plan") or {}, indent=2))
        archive.writestr("execution.json", json.dumps({k: v for k, v in evidence.items() if k != "screenshots"}, indent=2))
        for shot in evidence.get("screenshots") or []:
            archive.writestr(f"browser-{shot['width']}px.png", base64.b64decode(shot.get("png_base64") or "", validate=True))
    return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{package_id[:8]}-source.zip"'})


@app.get("/work-packages/{package_id}/preview")
def preview_web_artifact(package_id: str, request: Request):
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Previews are available only on this computer")
    from .intelligence.web_preview import preview
    files, _research = _verified_web_package(package_id)
    try:
        return {"url": preview(package_id, files), "storage": "browser profile on this computer", "package_id": package_id}
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Preview could not start. Its local port may be occupied: " + str(exc))


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

def _attach_goal(project_id: str, body: ObjectiveIn, duplicate: bool) -> None:
    if duplicate:
        return
    from .goals import open_goal, split_goal

    open_goal(
        project_id,
        list(body.acceptance_criteria or []),
        deadline=body.deadline,
        job_cap_gbp=body.job_cap_gbp,
        budget_usd=body.budget_usd,
        allow_paid_fallback=body.allow_paid_fallback,
        allow_publish=body.allow_publish,
    )
    from .milo_handoff import constraint_list

    split_goal(
        project_id,
        body.objective,
        list(body.acceptance_criteria or []),
        constraints=constraint_list(body.constraints),
    )


def _admit_budget(body: ObjectiveIn, idempotency_key: str) -> None:
    """Refuse a budget before the job exists: unsupported, or more than this month has left.

    A replay of a job that already exists is not a new spend, so it is let through
    to return that job. Nothing is clamped or converted to fit.
    """
    from .goals import check_budget
    from .intelligence.budget import month_allows_new_job

    cap_usd = check_budget(body.job_cap_gbp, body.budget_usd)
    if idempotency_key:
        import uuid as _uuid

        existing = _rows("SELECT id FROM projects WHERE id=?", (str(_uuid.uuid5(_uuid.NAMESPACE_URL, "ayven-project:" + idempotency_key)),))
        if existing:
            return
    allowed, why = month_allows_new_job(cap_usd)
    if not allowed:
        raise ValueError(f"{why} The job was not created and its budget was not changed.")


@app.post("/projects")
def create_project(body: ObjectiveIn, request: Request):
    from .milo_handoff import submit_job
    key = request.headers.get("Idempotency-Key", "").strip()
    try:
        # Refuse an unsupported budget before the job exists. It is never clamped.
        _admit_budget(body, key)
        result = submit_job(body.objective, key, body.title, constraints=body.constraints)
        _attach_goal(result["project_id"], body, bool(result.get("duplicate")))
        if not result.get("duplicate"):
            orchestrator.run_project(result["project_id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {**result, "replayed": result.get("duplicate", False)}

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
    from .durable import enabled
    try:
        if enabled():
            from .durable import answer_clarification as resume_clarification
            resume_clarification(package_id, body.answer)
        else:
            from .intelligence.execution import begin_clarification, start_clarification_worker
            begin_clarification(package_id, body.answer)
            start_clarification_worker(package_id)
    except KeyError:
        raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "package_id": package_id}

@app.get("/approvals")
def list_approvals():
    return {"approvals": _rows("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 50")}

@app.get("/budget")
def get_budget():
    from .intelligence.budget import budget_view

    return budget_view()


@app.put("/budget")
def put_budget(body: BudgetIn):
    import math

    from .intelligence.budget import set_monthly_cap_usd, usd_from_gbp

    if not math.isfinite(float(body.monthly_cap_gbp)) or body.monthly_cap_gbp <= 0:
        raise HTTPException(400, "Set a monthly budget above £0. The budget was not changed.")
    try:
        # The pound amount Lee set is kept exactly. Enforcement uses its dollar conversion.
        view = set_monthly_cap_usd(usd_from_gbp(body.monthly_cap_gbp), sent_gbp=float(body.monthly_cap_gbp))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return view


@app.post("/approvals/{aid}/resolve")
def resolve(aid: str, body: ApprovalIn):
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    if body.decision == "rejected" and not body.reason.strip():
        raise HTTPException(400, "Add a reason so Ayven knows what to change.")
    from .goals import ensure_schema, record_intervention

    ensure_schema()
    conn = connect()
    row = conn.execute("SELECT project_id, status FROM approvals WHERE id=?", (aid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404)
    if row["status"] != "pending":
        # Already resolved. Read before writing: a repeat is a no-op and a flip is
        # refused, and neither touches the reason, the intervention log or the state.
        conn.close()
        if row["status"] != body.decision:
            raise HTTPException(409, f"This approval was already {row['status']}; it can't be changed to {body.decision}.")
        return {"ok": True, "resumed": [], "unchanged": True}
    override = False
    if body.decision == "approved" and row["project_id"]:
        from .goals import criteria_evidence

        unmet = [item for item in criteria_evidence(row["project_id"]) if item["status"] == "not_met"]
        if unmet and not body.accept_unmet_criteria:
            conn.close()
            raise HTTPException(409, {
                "message": (f"{len(unmet)} acceptance criterion is not met" if len(unmet) == 1
                            else f"{len(unmet)} acceptance criteria are not met")
                + ". Nothing was approved. Reject it with a reason, or approve with accept_unmet_criteria and a reason.",
                "unmet_criteria": unmet,
            })
        if unmet and not body.reason.strip():
            conn.close()
            raise HTTPException(400, "Say why you are accepting a result with an unmet acceptance criterion.")
        override = bool(unmet)
    conn.execute(
        "UPDATE approvals SET reason=?, accepted_unmet=? WHERE id=?",
        (body.reason.strip(), 1 if override else 0, aid),
    )
    conn.commit()
    conn.close()
    if body.decision == "rejected" and row["project_id"]:
        record_intervention(row["project_id"], body.reason.strip())
    from .durable import enabled, resolve_approval as durable_resolve
    if enabled():
        try:
            return {"ok": True, "resumed": durable_resolve(aid, body.decision)}
        except KeyError:
            raise HTTPException(404)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
    try:
        orchestrator.resolve_approval(aid, body.decision)
    except KeyError:
        raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
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
def json_login(body: LoginIn, request: Request):
    import hashlib
    from fastapi.responses import JSONResponse
    if not protected():
        return {"ok": True, "protected": False}
    if body.key != access_key():
        raise HTTPException(401, "login required")
    response = JSONResponse({"ok": True, "protected": True})
    import time
    response.set_cookie("ayven_session", session_value(int(time.time())), max_age=TTL, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


@app.post("/milo/jobs")
def milo_submit(body: ObjectiveIn, request: Request, idempotency_key: str = ""):
    from .milo_handoff import submit_job
    header_key = request.headers.get("Idempotency-Key", "").strip()
    if header_key and idempotency_key and header_key != idempotency_key:
        raise HTTPException(400, "Idempotency keys disagree")
    try:
        # Refuse an unsupported budget before the job exists. It is never clamped.
        _admit_budget(body, header_key or idempotency_key)
        result = submit_job(
            body.objective,
            header_key or idempotency_key,
            body.title,
            constraints=body.constraints,
        )
        _attach_goal(result["project_id"], body, bool(result.get("duplicate")))
        if not result.get("duplicate"):
            orchestrator.run_project(result["project_id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


@app.get("/milo/jobs/{project_id}")
def milo_status(project_id: str):
    from .milo_handoff import job_status
    try:
        return job_status(project_id)
    except KeyError:
        raise HTTPException(404)


def _failed_package_report(package_id: str) -> bytes | None:
    """report.md for a FAILED package: fixed wording per failure type (app/failure_report.py).

    None when the package is not FAILED; 404 when there is nothing to report.
    """
    rows = _rows(
        "SELECT project_id, title, objective, task_class, workflow_state, findings, error, observability_json "
        "FROM work_packages WHERE id=?",
        (package_id,),
    )
    if not rows or (rows[0]["workflow_state"] or "") != "FAILED":
        return None
    row = rows[0]
    from . import failure_report
    from .goals import get_goal, list_blockers

    blockers = list_blockers(package_id=package_id)
    blockers.reverse()  # oldest first
    if not (row["error"] or "").strip() and not blockers and not (row["findings"] or "").strip():
        raise HTTPException(404, "This job didn't produce a report.")
    project = _rows("SELECT title, objective FROM projects WHERE id=?", (row["project_id"],))
    report = failure_report.build(
        task_class=row["task_class"] or "",
        error=row["error"] or "",
        blockers=blockers,
        evidence=failure_report.evidence_from(row["observability_json"] or ""),
        title=(project[0]["title"] if project else "") or row["title"] or "",
        brief=(project[0]["objective"] if project else "") or row["objective"] or "",
        goal=get_goal(row["project_id"]),
    )
    return report.encode("utf-8")


@app.get("/work-packages/{package_id}/bundle.zip")
def download_bundle(package_id: str):
    import io
    import zipfile
    from .blobs import list_files, load_bytes

    from .outcome import download_name, headers, outcome_for

    result = outcome_for(package_id=package_id)
    failed = _failed_package_report(package_id)
    if failed is not None:
        # A FAILED package ships only the report that explains the failure. Never source files.
        if result["state"] == "REJECTED":
            # Lee's rejection is not a step failure; the report says which it was.
            failed = (f"# REJECTED. NOT A DELIVERABLE.\n\n{result['reason']}\n\n").encode("utf-8") + failed
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("report.md", failed)
        name = download_name(package_id[:8], result, "bundle", "zip")
        return Response(content=buffer.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', **headers(result)})
    files = list_files(package_id)
    if not files:
        raise HTTPException(404, "This job didn't produce any files.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for item in files:
            content = load_bytes(package_id, item["name"])
            if content is None:
                raise HTTPException(404, "This file isn't available any more.")
            archive.writestr(item["name"], content)
        if result["state"] not in ("COMPLETED", "NEEDS_APPROVAL"):
            # Files from a blocked or unfinished job travel with a note saying they are not accepted.
            archive.writestr(
                "NOT-ACCEPTED.txt",
                f"{result['label']}. These files are not an accepted deliverable.\n{result['reason']}\n{result['next_action']}\n",
            )
    name = download_name(package_id[:8], result, "bundle", "zip")
    return Response(content=buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', **headers(result)})


@app.post("/work-packages/{package_id}/stop")
def stop_package(package_id: str):
    rows = _rows("SELECT project_id FROM work_packages WHERE id=?", (package_id,))
    if not rows:
        raise HTTPException(404)
    from .goals import stop_job

    return stop_job(rows[0]["project_id"])


@app.patch("/work-packages/{package_id}")
def patch_package(package_id: str, body: CapIn):
    rows = _rows("SELECT project_id FROM work_packages WHERE id=?", (package_id,))
    if not rows:
        raise HTTPException(404)
    from .goals import raise_cap

    try:
        return raise_cap(rows[0]["project_id"], body.job_cap_gbp)
    except KeyError:
        raise HTTPException(404)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/work-packages/{package_id}/accept")
def accept_package(package_id: str, body: AcceptIn | None = None):
    """Lee accepts a result.

    Approval rows are the gate a package waits on, so accepting a package that is
    waiting for approval resolves that approval through POST /approvals/{id}/resolve:
    one code path, the same resume, the same final state. Accept never moves a
    package that is not waiting for approval and not already complete.
    """
    rows = _rows("SELECT project_id, workflow_state FROM work_packages WHERE id=?", (package_id,))
    if not rows:
        raise HTTPException(404)
    project_id = rows[0]["project_id"]
    state = rows[0]["workflow_state"] or ""
    from .blobs import list_files
    from .goals import mark_complete
    from .intelligence.budget import record_intervention

    pending = _rows(
        "SELECT id FROM approvals WHERE project_id=? AND status='pending' ORDER BY created_at DESC",
        (project_id,),
    )
    if pending:
        body = body or AcceptIn()
        result = resolve(pending[0]["id"], ApprovalIn(
            decision="approved", reason=body.reason, accept_unmet_criteria=body.accept_unmet_criteria,
        ))
        record_intervention(project_id, "Lee accepted the result")
        after = _rows("SELECT workflow_state FROM work_packages WHERE id=?", (package_id,))
        final = (after[0]["workflow_state"] if after else "") or ""
        return {**result, "approval_id": pending[0]["id"], "workflow_state": final,
                "status": "completed" if final == "COMPLETED" else final.lower()}
    project = _rows("SELECT status FROM projects WHERE id=?", (project_id,))
    if state == "COMPLETED" and project and project[0]["status"] == "complete":
        # Already accepted or approved: a true no-op, nothing is logged or changed.
        return {"ok": True, "status": "completed", "workflow_state": state, "unchanged": True}
    if state != "COMPLETED":
        raise HTTPException(
            409,
            f"This package can't be accepted: it is {state or 'not finished'} and has no pending approval."
            " A failed package must be fixed and retried.",
        )
    names = [item["name"] for item in list_files(package_id)]
    try:
        result = mark_complete(project_id, names, True)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    record_intervention(project_id, "Lee accepted the result")
    return {**result, "workflow_state": state}


@app.post("/work-packages/{package_id}/rework")
def rework_package(package_id: str, body: ReworkIn):
    if not body.reason.strip():
        raise HTTPException(400, "Say what needs changing.")
    rows = _rows("SELECT project_id FROM work_packages WHERE id=?", (package_id,))
    if not rows:
        raise HTTPException(404)
    from .goals import add_blocker
    from .intelligence.budget import record_intervention

    record_intervention(rows[0]["project_id"], body.reason.strip())
    # Campus records the request. It does not re-run work on its own yet, so it says so
    # instead of promising a rework that never starts.
    blocker = add_blocker(
        rows[0]["project_id"], "rework", body.reason.strip(),
        "Rework is recorded but does not start automatically yet. Resubmit the brief with this reason.",
        package_id,
    )
    return {**blocker, "rework_started": False}


@app.get("/admin/backup")
def backup(request: Request):
    import hashlib
    import hmac
    import sqlite3
    import tempfile
    from .access import local_mode
    from .db import db_path
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not local_mode() and not protected():
        raise HTTPException(401, "Admin bearer token required")
    if protected() and not hmac.compare_digest(supplied.encode(), access_key().encode()):
        raise HTTPException(403, "Admin bearer token required")
    backup_dir = db_path().resolve().parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="ayven-", suffix=".db", dir=backup_dir)
    os.close(fd)
    target = Path(name)
    with sqlite3.connect(db_path()) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
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
