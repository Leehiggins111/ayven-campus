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
        attempts INTEGER NOT NULL DEFAULT 0, result_json TEXT, result_digest TEXT,
        created_at INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_artifacts (
        order_id TEXT NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY(order_id,name))""")

class ExecutionIn(BaseModel):
    objective: str = Field(min_length=1, max_length=12000)
    project_alias: str = "cloud-demo"
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    job_cap_gbp: float = Field(ge=0, le=10000)

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
    from .main import _admit_budget, ObjectiveIn, _attach_goal
    objective = ObjectiveIn(objective=body.objective, acceptance_criteria=body.acceptance_criteria,
        constraints=body.constraints, job_cap_gbp=body.job_cap_gbp)
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
            if existing["result_digest"] != fingerprint and existing["state"] == "queued":
                raise HTTPException(409, "Idempotency key payload mismatch")
            # Fingerprint is held in result_digest until completion; preserve comparison after completion separately.
            if existing["state"] != "queued":
                raise HTTPException(409, "Existing execution already started; use its original job id")
            c.commit()
            return {"project_id": existing["project_id"], "order_id": key, "duplicate": True}
        c.rollback()
        result = submit_job(body.objective, key, constraints=body.constraints)
        _attach_goal(result["project_id"], objective, bool(result.get("duplicate")))
        c = connect()
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        c.execute("""INSERT INTO workbench_orders
          (id,project_id,objective,project_alias,criteria_json,constraints_json,budget_gbp,state,result_digest,created_at)
          VALUES(?,?,?,?,?,?,?,'queued',?,?)""",
          (key,result["project_id"],body.objective,body.project_alias,json.dumps(body.acceptance_criteria),
           json.dumps(body.constraints),body.job_cap_gbp,fingerprint,int(time.time())))
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
    if body.verdict not in ("PASS","FAIL","BLOCKED") or body.execution_mode not in ("REAL","SIMULATED"):
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
        r=locked(c,order_id,body)
        for name,raw,digest in artifacts:
            c.execute("INSERT INTO workbench_artifacts VALUES(?,?,?,?)",(order_id,name,raw,digest))
        result_data={"verdict":body.verdict,"execution_mode":body.execution_mode,"evidence":body.evidence,
            "defects":body.defects,"artifacts":[{"name":n,"sha256":h} for n,_,h in artifacts]}
        c.execute("UPDATE workbench_orders SET state=?,result_json=?,lease_until=0 WHERE id=?",
            ("awaiting_approval" if body.verdict=="PASS" else "failed",json.dumps(result_data),order_id))
        c.commit()
        return {"ok":True,"state":"awaiting_approval" if body.verdict=="PASS" else "failed",
                "owner_accepted":False}
    finally:c.close()
