"""Version 1 pull-only Campus/Workbench bridge. No execution occurs in Campus."""
import hashlib
import hmac
import json
import os
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from .db import connect
from .workbench_scope import ScopeError, normalize as normalize_scope, verify_patch
from .workbench_evidence import test_evidence_problems

router = APIRouter()
MAX_ARTIFACT = 256 * 1024
LEASE_SECONDS = 90

def worker_authorized(request):
    secret = os.environ.get("AYVEN_WORKER_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    return len(secret) >= 32 and supplied.startswith("Bearer ") and hmac.compare_digest(supplied[7:].encode(), secret.encode())

def require_worker(request):
    if not worker_authorized(request):
        raise HTTPException(401, "Worker credential required")

def ensure_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_orders (
        id TEXT PRIMARY KEY, project_id TEXT UNIQUE NOT NULL, objective TEXT NOT NULL,
        project_alias TEXT NOT NULL, criteria_json TEXT NOT NULL, constraints_json TEXT NOT NULL,
        budget_gbp REAL NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
        worker TEXT, lease_until INTEGER NOT NULL DEFAULT 0, fence INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0, result_json TEXT, request_digest TEXT NOT NULL, result_digest TEXT,
        created_at INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_artifacts (
        order_id TEXT NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(order_id,name))""")
    columns = {row["name"] for row in c.execute("PRAGMA table_info(workbench_orders)").fetchall()}
    if "allowed_json" not in columns:
        # Orders created before C3 keep NULL: their scope cannot be verified, so they cannot be accepted.
        c.execute("ALTER TABLE workbench_orders ADD COLUMN allowed_json TEXT")

class ExecutionIn(BaseModel):
    objective: str = Field(min_length=1, max_length=12000)
    project_alias: str = "cloud-demo"
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    job_cap_gbp: float = Field(ge=0, le=10000)
    allowed_files: list[str] | None = None   # required; validated with the exact-path rules below

class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)

class LeaseIn(BaseModel):
    worker_id: str
    fence: int = Field(ge=1)

class ArtifactIn(BaseModel):
    name: str
    content: str
    sha256: str

class ResultIn(LeaseIn):
    verdict: str
    execution_mode: str
    evidence: list[str] = Field(default_factory=list)
    defects: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactIn] = Field(default_factory=list)

@router.post("/milo/executions")
def enqueue(body: ExecutionIn, request: Request):
    # The normal Campus boundary protects this owner-facing route.
    if body.project_alias != "cloud-demo":
        raise HTTPException(400, "Project alias not allowlisted")
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 200:
        raise HTTPException(400, "Idempotency-Key required (max 200 characters)")
    if not body.acceptance_criteria:
        raise HTTPException(400, "Acceptance criteria required")
    try:
        allowed = normalize_scope(body.allowed_files)
    except ScopeError as exc:
        raise HTTPException(400, str(exc))
    body.allowed_files = allowed
    from .main import _admit_budget, ObjectiveIn, _attach_goal
    objective = ObjectiveIn(objective=body.objective, acceptance_criteria=body.acceptance_criteria,
        constraints=body.constraints, job_cap_gbp=body.job_cap_gbp)
    # A zero-cap Workbench order uses the local, no-paid-API execution path.
    # Campus budget admission requires a strictly positive cap, so do not
    # submit zero-cost jobs to that paid-budget validator.
    if body.job_cap_gbp > 0:
        _admit_budget(objective, key)
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    from .milo_handoff import submit_job
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        existing = c.execute("SELECT * FROM workbench_orders WHERE id=?", (key,)).fetchone()
        if existing:
            if existing["request_digest"] != fingerprint:
                raise HTTPException(409, "Idempotency key payload mismatch")
            # Fingerprint is held in result_digest until completion; preserve comparison after completion separately.
            c.commit()
            return {"project_id": existing["project_id"], "order_id": key, "duplicate": True, "state": existing["state"]}
        c.rollback()
        result = submit_job(body.objective, key, constraints=body.constraints)
        if body.job_cap_gbp > 0:
            _attach_goal(result["project_id"], objective, bool(result.get("duplicate")))
        elif not result.get("duplicate"):
            # The generic goal contract assumes a paid budget. Keep zero-cost
            # Workbench criteria in workbench_orders and split tasks separately.
            from .goals import split_goal
            split_goal(result["project_id"], body.objective,
                       list(body.acceptance_criteria), constraints=list(body.constraints))
        c = connect()
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        c.execute("""INSERT INTO workbench_orders
          (id,project_id,objective,project_alias,criteria_json,constraints_json,budget_gbp,state,request_digest,
           created_at,allowed_json)
          VALUES(?,?,?,?,?,?,?,'queued',?,?,?)""",
          (key,result["project_id"],body.objective,body.project_alias,json.dumps(body.acceptance_criteria),
           json.dumps(body.constraints),body.job_cap_gbp,fingerprint,int(time.time()),json.dumps(allowed)))
        c.commit()
        return {"project_id":result["project_id"],"order_id":key,"duplicate":False,"mode":"PENDING_REAL"}
    finally:
        c.close()

@router.post("/internal/workbench/claim")
def claim(body: ClaimIn, request: Request):
    require_worker(request)
    c=connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        now=int(time.time())
        # Expired final attempts must become terminal, not remain running forever.
        c.execute("""UPDATE workbench_orders SET state='failed',lease_until=0,
            result_json=? WHERE state='running' AND lease_until < ? AND attempts >= 3""",
            (json.dumps({"verdict":"BLOCKED","execution_mode":"UNKNOWN","evidence":[],
             "defects":["Worker lease expired after maximum attempts"],"artifacts":[]}),now))
        r=c.execute("""SELECT * FROM workbench_orders WHERE state='queued'
          OR (state='running' AND lease_until < ? AND attempts < 3)
          ORDER BY created_at LIMIT 1""",(now,)).fetchone()
        if not r:
            c.commit()
            return {"job":None}
        fence=r["fence"]+1
        c.execute("""UPDATE workbench_orders SET state='running',worker=?,fence=?,lease_until=?,attempts=attempts+1
            WHERE id=?""",(body.worker_id,fence,now+LEASE_SECONDS,r["id"]))
        c.commit()
        return {"job":{"order_id":r["id"],"project_id":r["project_id"],"objective":r["objective"],
           "project_alias":r["project_alias"],"acceptance_criteria":json.loads(r["criteria_json"]),
           "constraints":json.loads(r["constraints_json"]),"budget_gbp":r["budget_gbp"],
           "allowed_files":json.loads(r["allowed_json"]) if r["allowed_json"] else None,
           "fence":fence,"lease_seconds":LEASE_SECONDS}}
    finally:
        c.close()

def locked(c,order_id,body):
    r=c.execute("SELECT * FROM workbench_orders WHERE id=?",(order_id,)).fetchone()
    if not r: raise HTTPException(404,"Unknown order")
    if r["state"]!="running" or r["worker"]!=body.worker_id or r["fence"]!=body.fence or r["lease_until"] < int(time.time()):
        raise HTTPException(409,"Stale worker lease")
    return r

@router.post("/internal/workbench/{order_id}/heartbeat")
def heartbeat(order_id:str,body:LeaseIn,request:Request):
    require_worker(request)
    c=connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        locked(c,order_id,body)
        c.execute("UPDATE workbench_orders SET lease_until=? WHERE id=?",(int(time.time())+LEASE_SECONDS,order_id))
        c.commit()
        return {"ok":True}
    finally:c.close()

@router.post("/internal/workbench/{order_id}/result")
def result(order_id:str,body:ResultIn,request:Request):
    require_worker(request)
    if body.verdict not in ("PASS","FAIL","BLOCKED"):
        raise HTTPException(400,"Invalid verdict or mode")
    # NONE means nothing ran (C12): never labelled REAL. Only a PASS must say how it was produced.
    if body.execution_mode not in (("REAL","SIMULATED") if body.verdict=="PASS" else ("REAL","SIMULATED","NONE")):
        raise HTTPException(400,"Invalid verdict or mode")
    if body.verdict=="PASS" and (not body.evidence or body.defects):
        raise HTTPException(400,"PASS requires evidence and no defects")
    artifacts=[]
    for a in body.artifacts:
        if not a.name or "/" in a.name or "\\" in a.name or a.name in (".",".."):
            raise HTTPException(400,"Unsafe artifact name")
        raw=a.content.encode("utf-8")
        if len(raw)>MAX_ARTIFACT or hashlib.sha256(raw).hexdigest()!=a.sha256:
            raise HTTPException(400,"Artifact size/hash mismatch")
        artifacts.append((a.name,raw,a.sha256))
    if len(artifacts)>10 or len({x[0] for x in artifacts})!=len(artifacts):
        raise HTTPException(400,"Too many or duplicate artifacts")
    c=connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        prior=c.execute("SELECT state,worker,fence,result_digest FROM workbench_orders WHERE id=?",(order_id,)).fetchone()
        if prior and prior["state"] in ("awaiting_approval","completed","rejected","failed","simulated"):
            candidate={"verdict":body.verdict,"execution_mode":body.execution_mode,
                "evidence":body.evidence,"defects":body.defects,
                "artifacts":[{"name":n,"sha256":h} for n,_,h in artifacts]}
            digest=hashlib.sha256(json.dumps(candidate,sort_keys=True).encode()).hexdigest()
            if prior["worker"]==body.worker_id and prior["fence"]==body.fence and prior["result_digest"]==digest:
                c.commit()
                return {"ok":True,"duplicate":True,"state":prior["state"],
                        "owner_accepted":prior["state"]=="completed"}
            raise HTTPException(409,"Result already finalized or mismatched")
        r=locked(c,order_id,body)
        for name,raw,digest in artifacts:
            c.execute("INSERT INTO workbench_artifacts VALUES(?,?,?,?)",(order_id,name,raw,digest))
        submitted={"verdict":body.verdict,"execution_mode":body.execution_mode,"evidence":body.evidence,
            "defects":body.defects,"artifacts":[{"name":n,"sha256":h} for n,_,h in artifacts]}
        result_data=dict(submitted, scope_verified=False, tests_verified=False)
        if body.verdict=="PASS":
            # The worker's PASS is a claim. Campus checks the patch's scope (C3) and that the Workbench's own
            # test run passed on exactly this patch, from the complete log, before anyone may approve it.
            allowed=json.loads(r["allowed_json"]) if r["allowed_json"] else None
            files={n:raw for n,raw,_ in artifacts}
            scope=verify_patch(allowed,files)
            tests=test_evidence_problems(files)
            if scope or tests:
                result_data.update(verdict="FAIL",worker_verdict="PASS",campus_rejected=True,
                                   defects=["Campus scope check: "+p for p in scope]+
                                           ["Campus test-evidence check: "+p for p in tests])
            result_data.update(scope_verified=not scope,tests_verified=not tests)
        if result_data["verdict"]!="PASS":
            state="failed"
        elif body.execution_mode!="REAL":
            # Simulated or sample work is recorded and shown, but it never waits for, or accepts, approval.
            state="simulated"
        else:
            state="awaiting_approval"
        # The digest is of what the worker sent, so an identical replay stays idempotent after a downgrade.
        c.execute("UPDATE workbench_orders SET state=?,result_json=?,result_digest=?,lease_until=0 WHERE id=?",
            (state,json.dumps(result_data),
             hashlib.sha256(json.dumps(submitted,sort_keys=True).encode()).hexdigest(),order_id))
        c.commit()
        return {"ok":True,"state":state,"owner_accepted":False,
                "campus_rejected":bool(result_data.get("campus_rejected"))}
    finally:c.close()

@router.get("/milo/executions/{order_id}")
def execution_status(order_id:str):
    c=connect()
    try:
        ensure_tables(c)
        r=c.execute("SELECT * FROM workbench_orders WHERE id=?",(order_id,)).fetchone()
        if not r: raise HTTPException(404,"Unknown execution")
        return {"order_id":order_id,"project_id":r["project_id"],"state":r["state"],
            "result":json.loads(r["result_json"]) if r["result_json"] else None,
            "owner_accepted":r["state"]=="completed"}
    finally:c.close()

class DecisionIn(BaseModel):
    decision: str
    reason: str = ""

@router.post("/milo/executions/{order_id}/decision")
def owner_decision(order_id:str,body:DecisionIn):
    if body.decision not in ("accept","reject"):
        raise HTTPException(400,"Expected accept or reject")
    if body.decision=="reject" and not body.reason.strip():
        raise HTTPException(400,"Rejection reason required")
    c=connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        r=c.execute("SELECT * FROM workbench_orders WHERE id=?",(order_id,)).fetchone()
        if not r: raise HTTPException(404,"Unknown execution")
        if r["state"]=="simulated":
            raise HTTPException(409,"Simulated or sample work cannot be approved or rejected; it is a record only")
        if r["state"]!="awaiting_approval":
            raise HTTPException(409,"Execution not awaiting approval")
        report=json.loads(r["result_json"] or "{}")
        if report.get("verdict")!="PASS":
            raise HTTPException(409,"Only QA PASS may be accepted")
        if body.decision=="accept" and report.get("execution_mode")!="REAL":
            raise HTTPException(409,"Simulated work cannot be accepted as a real deliverable")
        if body.decision=="accept" and report.get("scope_verified") is not True:
            # Includes results recorded before C3: their changed files were never checked by Campus.
            raise HTTPException(409,"Campus has not verified which files this result changed; it cannot be accepted")
        if body.decision=="accept" and report.get("tests_verified") is not True:
            # Includes results recorded before this check: their tests were only claimed by the worker.
            raise HTTPException(409,"Campus has not verified the test run for this result; it cannot be accepted")
        c.execute("UPDATE workbench_orders SET state=? WHERE id=?",
            ("completed" if body.decision=="accept" else "rejected",order_id))
        c.commit()
        return {"state":"completed" if body.decision=="accept" else "rejected"}
    finally:c.close()

@router.get("/milo/executions/{order_id}/artifacts/{name}")
def download_artifact(order_id:str,name:str):
    from fastapi.responses import Response
    c=connect()
    try:
        ensure_tables(c)
        r=c.execute("SELECT state FROM workbench_orders WHERE id=?",(order_id,)).fetchone()
        if not r: raise HTTPException(404,"Unknown execution")
        if r["state"]!="completed":
            raise HTTPException(409,"Owner acceptance required")
        a=c.execute("SELECT content,sha256 FROM workbench_artifacts WHERE order_id=? AND name=?",
            (order_id,name)).fetchone()
        if not a: raise HTTPException(404,"Unknown artifact")
        return Response(content=a["content"],media_type="application/octet-stream",
            headers={"X-Content-SHA256":a["sha256"],"Cache-Control":"no-store"})
    finally:c.close()
