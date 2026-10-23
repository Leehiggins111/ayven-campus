"""Version 1 pull-only Campus/Workbench bridge. No execution occurs in Campus.

Campus is the authority for an order's lifecycle; see docs/contracts/workbench-execution-v1.md.

Who may move an order between states:
  owner  : create (queued), cancel (-> cancelled), revise (failed|rejected|needs_clarification -> queued, new
           revision), accept / reject (awaiting_approval -> completed | rejected)
  worker : claim (queued -> running, lease + fence), heartbeat (keeps the lease, reports progress), result
           (running -> awaiting_approval | failed | simulated | needs_clarification)
  Campus : lease expiry (running -> queued for a new fence, or failed after MAX_ATTEMPTS), time and token
           limits (running -> failed)
A worker can only act while it holds the current fence of a live lease; anything else is a 409.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from .db import connect
from .workbench_scope import ScopeError, normalize as normalize_scope, verify_patch
from .workbench_evidence import test_evidence_problems, request_key

router = APIRouter()
MAX_ARTIFACT = 256 * 1024
LEASE_SECONDS = 90   # default; AYVEN_WORKBENCH_LEASE_SECONDS (15..600) for local drills


def lease_seconds():
    try:
        return max(15, min(600, int(os.environ.get("AYVEN_WORKBENCH_LEASE_SECONDS", LEASE_SECONDS))))
    except ValueError:
        return LEASE_SECONDS
MAX_ATTEMPTS = 3
DEFAULT_MINUTES = 30
PREVIEWABLE = ("changes.diff", "qa-run.json", "qa-test.log", "baseline-test.log")
FINAL_STATES = ("awaiting_approval", "completed", "rejected", "failed", "simulated", "needs_clarification",
                "cancelled")


def worker_authorized(request):
    secret = os.environ.get("AYVEN_WORKER_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    return len(secret) >= 32 and supplied.startswith("Bearer ") and hmac.compare_digest(supplied[7:].encode(), secret.encode())


def require_worker(request):
    if not worker_authorized(request):
        raise HTTPException(401, "Worker credential required")


COLUMNS = {   # added after the first release; NULL/defaults for older rows
    "allowed_json": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 0", "revisions_json": "TEXT NOT NULL DEFAULT '[]'",
    "max_minutes": "INTEGER", "token_cap": "INTEGER", "allow_clarification": "INTEGER NOT NULL DEFAULT 0",
    "started_at": "INTEGER", "usage_json": "TEXT", "question": "TEXT", "updated_at": "INTEGER",
}


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
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_events (
        order_id TEXT NOT NULL, seq INTEGER NOT NULL, ts INTEGER NOT NULL, revision INTEGER NOT NULL,
        kind TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY(order_id,seq))""")
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_history (
        order_id TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL, result_json TEXT,
        artifacts_json TEXT NOT NULL, archived_at INTEGER NOT NULL, PRIMARY KEY(order_id,revision))""")
    columns = {row["name"] for row in c.execute("PRAGMA table_info(workbench_orders)").fetchall()}
    for name, kind in COLUMNS.items():
        if name not in columns:
            # Orders created before C3 keep a NULL allow-list: their scope cannot be verified, so never accepted.
            c.execute(f"ALTER TABLE workbench_orders ADD COLUMN {name} {kind}")


def event(c, order_id, kind, actor, text, revision=None):
    """Durable progress/audit event, written inside the caller's transaction."""
    if revision is None:
        row = c.execute("SELECT revision FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        revision = row["revision"] if row else 0
    seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM workbench_events WHERE order_id=?", (order_id,)).fetchone()[0]
    c.execute("INSERT INTO workbench_events VALUES(?,?,?,?,?,?,?)",
              (order_id, seq, int(time.time()), revision or 0, kind, actor, str(text)[:2000]))


class ExecutionIn(BaseModel):
    objective: str = Field(min_length=1, max_length=12000)
    project_alias: str = "cloud-demo"
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    job_cap_gbp: float = Field(ge=0, le=10000)
    allowed_files: list[str] | None = None   # required; validated with the exact-path rules below
    max_minutes: int = Field(default=DEFAULT_MINUTES, ge=1, le=240)      # wall clock per revision
    token_cap: int | None = Field(default=None, ge=500, le=5_000_000)    # model tokens per revision
    allow_clarification: bool = False                                    # may the agent pause with a question?


class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)


class LeaseIn(BaseModel):
    worker_id: str
    fence: int = Field(ge=1)


class ProgressIn(BaseModel):
    stage: str = Field(default="", max_length=300)
    status: str | None = Field(default=None, max_length=60)
    tokens: int = Field(default=0, ge=0)
    cost_gbp: float = Field(default=0, ge=0)


class HeartbeatIn(LeaseIn):
    progress: ProgressIn | None = None


class ArtifactIn(BaseModel):
    name: str
    content: str
    sha256: str


class UsageIn(BaseModel):
    tokens: int = Field(default=0, ge=0)
    cost_gbp: float = Field(default=0, ge=0)


class ResultIn(LeaseIn):
    verdict: str
    execution_mode: str
    evidence: list[str] = Field(default_factory=list)
    defects: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactIn] = Field(default_factory=list)
    question: str = Field(default="", max_length=1000)
    usage: UsageIn | None = None


def _fingerprint(body):
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    # Fields added later are left out at their defaults, so replays of older orders keep their fingerprint.
    for name, default in (("max_minutes", DEFAULT_MINUTES), ("token_cap", None), ("allow_clarification", False)):
        if payload.get(name) == default:
            payload.pop(name, None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _duplicate(existing, fingerprint, key):
    if existing["request_digest"] != fingerprint:
        raise HTTPException(409, "Idempotency key payload mismatch")
    return {"project_id": existing["project_id"], "order_id": key, "duplicate": True, "state": existing["state"]}


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
    fingerprint = _fingerprint(body)
    from .milo_handoff import submit_job
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        existing = c.execute("SELECT * FROM workbench_orders WHERE id=?", (key,)).fetchone()
        c.commit()
        if existing:
            return _duplicate(existing, fingerprint, key)
    finally:
        c.close()
    # A zero-cap Workbench order uses the local, no-paid-API execution path. Campus budget admission requires
    # a strictly positive cap, so zero-cost jobs skip that paid-budget validator.
    if body.job_cap_gbp > 0:
        _admit_budget(objective, key)
    # submit_job is idempotent by key: a crash after it and before the INSERT below is repaired by the replay.
    result = submit_job(body.objective, key, constraints=body.constraints)
    if body.job_cap_gbp > 0:
        _attach_goal(result["project_id"], objective, bool(result.get("duplicate")))
    elif not result.get("duplicate"):
        # The generic goal contract assumes a paid budget. Keep zero-cost Workbench criteria in workbench_orders.
        from .goals import split_goal
        split_goal(result["project_id"], body.objective,
                   list(body.acceptance_criteria), constraints=list(body.constraints))
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        existing = c.execute("SELECT * FROM workbench_orders WHERE id=?", (key,)).fetchone()
        if existing:   # a concurrent duplicate request got here first: exactly one order, reported as duplicate
            c.commit()
            return _duplicate(existing, fingerprint, key)
        now = int(time.time())
        try:
            c.execute("""INSERT INTO workbench_orders
              (id,project_id,objective,project_alias,criteria_json,constraints_json,budget_gbp,state,request_digest,
               created_at,allowed_json,max_minutes,token_cap,allow_clarification,updated_at)
              VALUES(?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?)""",
              (key, result["project_id"], body.objective, body.project_alias, json.dumps(body.acceptance_criteria),
               json.dumps(body.constraints), body.job_cap_gbp, fingerprint, now, json.dumps(allowed),
               body.max_minutes, body.token_cap, int(body.allow_clarification), now))
        except sqlite3.IntegrityError:
            c.rollback()
            c.close()
            c = connect()
            existing = c.execute("SELECT * FROM workbench_orders WHERE id=?", (key,)).fetchone()
            if existing:
                return _duplicate(existing, fingerprint, key)
            raise HTTPException(409, "Order conflicts with an existing project")
        event(c, key, "created", "owner", f"Order created; allowed files: {', '.join(allowed)}; "
              f"limits: {body.max_minutes} min" + (f", {body.token_cap} tokens" if body.token_cap else ""), 0)
        c.commit()
        return {"project_id": result["project_id"], "order_id": key, "duplicate": False, "mode": "PENDING_REAL",
                "state": "queued"}
    finally:
        c.close()


def _expire(c, now):
    """Campus-owned transitions: expired leases and wall-clock limits."""
    for r in c.execute("SELECT * FROM workbench_orders WHERE state='running' AND lease_until < ?", (now,)).fetchall():
        if r["attempts"] >= MAX_ATTEMPTS:
            _fail(c, r, f"Worker lease expired after {MAX_ATTEMPTS} attempts", "campus")
        else:
            c.execute("UPDATE workbench_orders SET state='queued',lease_until=0,updated_at=? WHERE id=?", (now, r["id"]))
            event(c, r["id"], "lease_expired", "campus",
                  f"Worker {r['worker']} stopped heartbeating (fence {r['fence']}); re-queued for another attempt")
    for r in c.execute("SELECT * FROM workbench_orders WHERE state='running' AND started_at IS NOT NULL").fetchall():
        if now > r["started_at"] + 60 * (r["max_minutes"] or DEFAULT_MINUTES):
            _fail(c, r, f"Time limit reached: {r['max_minutes'] or DEFAULT_MINUTES} minutes", "campus")


def _fail(c, r, reason, actor):
    data = {"verdict": "BLOCKED", "execution_mode": "NONE", "evidence": [], "defects": [reason], "artifacts": [],
            "scope_verified": False, "tests_verified": False}
    c.execute("UPDATE workbench_orders SET state='failed',lease_until=0,result_json=?,updated_at=? WHERE id=?",
              (json.dumps(data), int(time.time()), r["id"]))
    event(c, r["id"], "failed", actor, reason)


@router.post("/internal/workbench/claim")
def claim(body: ClaimIn, request: Request):
    require_worker(request)
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        now = int(time.time())
        _expire(c, now)
        r = c.execute("SELECT * FROM workbench_orders WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not r:
            c.commit()
            return {"job": None}
        fence = r["fence"] + 1
        c.execute("""UPDATE workbench_orders SET state='running',worker=?,fence=?,lease_until=?,attempts=attempts+1,
            started_at=COALESCE(started_at,?),updated_at=? WHERE id=?""",
            (body.worker_id, fence, now + lease_seconds(), now, now, r["id"]))
        event(c, r["id"], "claimed", "worker", f"Claimed by {body.worker_id} (attempt {r['attempts'] + 1}, fence {fence})")
        c.commit()
        started = r["started_at"] or now
        return {"job": {"order_id": r["id"], "project_id": r["project_id"], "objective": r["objective"],
           "project_alias": r["project_alias"], "acceptance_criteria": json.loads(r["criteria_json"]),
           "constraints": json.loads(r["constraints_json"]), "budget_gbp": r["budget_gbp"],
           "allowed_files": json.loads(r["allowed_json"]) if r["allowed_json"] else None,
           "fence": fence, "lease_seconds": lease_seconds(), "revision": r["revision"] or 0,
           "revisions": json.loads(r["revisions_json"] or "[]"), "request_key": request_key(r["id"], r["revision"] or 0),
           "max_minutes": r["max_minutes"] or DEFAULT_MINUTES,
           "deadline": started + 60 * (r["max_minutes"] or DEFAULT_MINUTES),
           "token_cap": r["token_cap"], "allow_clarification": bool(r["allow_clarification"])}}
    finally:
        c.close()


def locked(c, order_id, body):
    r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown order")
    if r["state"] == "cancelled":
        raise HTTPException(409, "Order cancelled by the owner")
    if r["state"] != "running" or r["worker"] != body.worker_id or r["fence"] != body.fence or r["lease_until"] < int(time.time()):
        raise HTTPException(409, "Stale worker lease")
    return r


@router.post("/internal/workbench/{order_id}/heartbeat")
def heartbeat(order_id: str, body: HeartbeatIn, request: Request):
    require_worker(request)
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        now = int(time.time())
        r = locked(c, order_id, body)
        limit = 60 * (r["max_minutes"] or DEFAULT_MINUTES)
        if r["started_at"] and now > r["started_at"] + limit:
            _fail(c, r, f"Time limit reached: {r['max_minutes'] or DEFAULT_MINUTES} minutes", "campus")
            c.commit()
            raise HTTPException(409, "Time limit reached")
        p = body.progress
        if p:
            usage = {"tokens": p.tokens, "cost_gbp": p.cost_gbp}
            if r["token_cap"] and p.tokens >= r["token_cap"]:
                _fail(c, r, f"Budget exhausted: {p.tokens} of {r['token_cap']} model tokens", "campus")
                c.execute("UPDATE workbench_orders SET usage_json=? WHERE id=?", (json.dumps(usage), order_id))
                c.commit()
                raise HTTPException(409, "Budget exhausted")
            if r["budget_gbp"] and p.cost_gbp > r["budget_gbp"]:
                _fail(c, r, f"Budget exhausted: £{p.cost_gbp:.2f} of £{r['budget_gbp']:.2f}", "campus")
                c.commit()
                raise HTTPException(409, "Budget exhausted")
            last = c.execute("SELECT text FROM workbench_events WHERE order_id=? AND kind='progress' "
                             "ORDER BY seq DESC LIMIT 1", (order_id,)).fetchone()
            if p.stage and (not last or last["text"] != p.stage):
                event(c, order_id, "progress", "worker", p.stage)
            c.execute("UPDATE workbench_orders SET usage_json=? WHERE id=?", (json.dumps(usage), order_id))
        c.execute("UPDATE workbench_orders SET lease_until=?,updated_at=? WHERE id=?", (now + lease_seconds(), now, order_id))
        c.commit()
        return {"ok": True, "deadline": (r["started_at"] or now) + limit}
    finally:
        c.close()


@router.post("/internal/workbench/{order_id}/result")
def result(order_id: str, body: ResultIn, request: Request):
    require_worker(request)
    if body.verdict not in ("PASS", "FAIL", "BLOCKED", "CLARIFY"):
        raise HTTPException(400, "Invalid verdict or mode")
    # NONE means nothing ran (C12): never labelled REAL. Only a PASS must say how it was produced.
    modes = ("REAL", "SIMULATED") if body.verdict == "PASS" else \
        ("NONE",) if body.verdict == "CLARIFY" else ("REAL", "SIMULATED", "NONE")
    if body.execution_mode not in modes:
        raise HTTPException(400, "Invalid verdict or mode")
    if body.verdict == "PASS" and (not body.evidence or body.defects):
        raise HTTPException(400, "PASS requires evidence and no defects")
    if body.verdict == "CLARIFY" and (not body.question.strip() or body.artifacts):
        raise HTTPException(400, "CLARIFY requires a question and no artifacts")
    artifacts = []
    for a in body.artifacts:
        if not a.name or "/" in a.name or "\\" in a.name or a.name in (".", ".."):
            raise HTTPException(400, "Unsafe artifact name")
        raw = a.content.encode("utf-8", "surrogateescape")
        if len(raw) > MAX_ARTIFACT or hashlib.sha256(raw).hexdigest() != a.sha256:
            raise HTTPException(400, "Artifact size/hash mismatch")
        artifacts.append((a.name, raw, a.sha256))
    if len(artifacts) > 10 or len({x[0] for x in artifacts}) != len(artifacts):
        raise HTTPException(400, "Too many or duplicate artifacts")
    submitted = {"verdict": body.verdict, "execution_mode": body.execution_mode, "evidence": body.evidence,
                 "defects": body.defects, "artifacts": [{"name": n, "sha256": h} for n, _, h in artifacts]}
    if body.verdict == "CLARIFY":
        submitted["question"] = body.question
    digest = hashlib.sha256(json.dumps(submitted, sort_keys=True).encode()).hexdigest()
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        prior = c.execute("SELECT state,worker,fence,result_digest FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if prior and prior["state"] in FINAL_STATES:
            if prior["worker"] == body.worker_id and prior["fence"] == body.fence and prior["result_digest"] == digest:
                c.commit()
                return {"ok": True, "duplicate": True, "state": prior["state"],
                        "owner_accepted": prior["state"] == "completed"}
            raise HTTPException(409, "Result already finalized or mismatched")
        r = locked(c, order_id, body)
        c.execute("DELETE FROM workbench_artifacts WHERE order_id=?", (order_id,))
        for name, raw, sha in artifacts:
            c.execute("INSERT INTO workbench_artifacts VALUES(?,?,?,?)", (order_id, name, raw, sha))
        result_data = dict(submitted, scope_verified=False, tests_verified=False, revision=r["revision"] or 0)
        if body.verdict == "PASS":
            # The worker's PASS is a claim. Campus checks the patch's scope (C3) and the Workbench's signed
            # verification of exactly this patch, for this order and revision, before anyone may approve it.
            allowed = json.loads(r["allowed_json"]) if r["allowed_json"] else None
            files = {n: raw for n, raw, _ in artifacts}
            scope = verify_patch(allowed, files)
            tests = test_evidence_problems(files, order_id=order_id, revision=r["revision"] or 0, allowed=allowed)
            if scope or tests:
                result_data.update(verdict="FAIL", worker_verdict="PASS", campus_rejected=True,
                                   defects=["Campus scope check: " + p for p in scope] +
                                           ["Campus test-evidence check: " + p for p in tests])
            result_data.update(scope_verified=not scope, tests_verified=not tests)
        if body.verdict == "CLARIFY":
            state = "needs_clarification" if r["allow_clarification"] else "failed"
            if state == "failed":
                result_data.update(verdict="FAIL", defects=["The agent asked a question but this order does not "
                                                            "allow clarification: " + body.question])
        elif result_data["verdict"] != "PASS":
            state = "failed"
        elif body.execution_mode != "REAL":
            # Simulated or sample work is recorded and shown, but it never waits for, or accepts, approval.
            state = "simulated"
        else:
            state = "awaiting_approval"
        usage = body.usage.model_dump() if body.usage else None
        # The digest is of what the worker sent, so an identical replay stays idempotent after a downgrade.
        c.execute("""UPDATE workbench_orders SET state=?,result_json=?,result_digest=?,lease_until=0,question=?,
            usage_json=COALESCE(?,usage_json),updated_at=? WHERE id=?""",
            (state, json.dumps(result_data), digest, body.question if state == "needs_clarification" else None,
             json.dumps(usage) if usage else None, int(time.time()), order_id))
        event(c, order_id, "result", "worker", f"{body.verdict} ({body.execution_mode}) -> {state}" +
              (": " + "; ".join(result_data.get("defects") or [])[:600] if result_data.get("defects") else "") +
              (": " + body.question if state == "needs_clarification" else ""))
        c.commit()
        return {"ok": True, "state": state, "owner_accepted": False,
                "campus_rejected": bool(result_data.get("campus_rejected"))}
    finally:
        c.close()


def _view(r):
    report = json.loads(r["result_json"]) if r["result_json"] else None
    verified = bool(report and report.get("verdict") == "PASS" and report.get("scope_verified") is True
                    and report.get("tests_verified") is True)
    return {"order_id": r["id"], "project_id": r["project_id"], "objective": r["objective"], "state": r["state"],
            "allowed_files": json.loads(r["allowed_json"]) if r["allowed_json"] else None,
            "attempts": r["attempts"], "created_at": r["created_at"], "updated_at": r["updated_at"],
            "revision": r["revision"] or 0, "revisions": json.loads(r["revisions_json"] or "[]"),
            "question": r["question"], "usage": json.loads(r["usage_json"]) if r["usage_json"] else None,
            "limits": {"max_minutes": r["max_minutes"] or DEFAULT_MINUTES, "token_cap": r["token_cap"],
                       "job_cap_gbp": r["budget_gbp"]},
            "verification": "VERIFIED" if verified else "UNVERIFIED",
            "result": report, "owner_accepted": r["state"] == "completed"}


@router.get("/milo/executions")
def list_executions(limit: int = 50):
    """Owner-visible durable queue, including interruptions and independent QA verdicts.
    Source of truth is workbench_orders; no worker-provided artifact content is inlined.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(400, "Limit must be between 1 and 100")
    c = connect()
    try:
        ensure_tables(c)
        rows = c.execute("SELECT * FROM workbench_orders ORDER BY created_at DESC,id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for r in rows:
            v, verdict = _view(r), json.loads(r["result_json"]) if r["result_json"] else {}
            result.append({
                "order_id": r["id"], "project_id": r["project_id"], "objective": r["objective"], "state": r["state"],
                "allowed_files": v["allowed_files"], "attempts": r["attempts"], "created_at": r["created_at"],
                "revision": v["revision"], "question": r["question"], "verification": v["verification"],
                "verdict": verdict.get("verdict"), "execution_mode": verdict.get("execution_mode"),
                "scope_verified": verdict.get("scope_verified") is True,
                "tests_verified": verdict.get("tests_verified") is True,
                "defects": verdict.get("defects", [])[:10], "artifacts": verdict.get("artifacts", []),
            })
        return {"executions": result}
    finally:
        c.close()


@router.get("/milo/executions/{order_id}")
def execution_status(order_id: str):
    c = connect()
    try:
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        return _view(r)
    finally:
        c.close()


@router.get("/milo/executions/{order_id}/events")
def execution_events(order_id: str, after: int = 0, limit: int = 200):
    if limit < 1 or limit > 500:
        raise HTTPException(400, "Limit must be between 1 and 500")
    c = connect()
    try:
        ensure_tables(c)
        if not c.execute("SELECT 1 FROM workbench_orders WHERE id=?", (order_id,)).fetchone():
            raise HTTPException(404, "Unknown execution")
        rows = c.execute("SELECT seq,ts,revision,kind,actor,text FROM workbench_events WHERE order_id=? AND seq>? "
                         "ORDER BY seq LIMIT ?", (order_id, after, limit)).fetchall()
        return {"events": [dict(x) for x in rows]}
    finally:
        c.close()


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/milo/executions/{order_id}/cancel")
def cancel_execution(order_id: str, body: CancelIn):
    """Owner cancel. Queued or running work becomes cancelled at once (terminal); the worker learns on its next
    heartbeat (409), stops the Workbench goal, and any later result from it is refused. Idempotent."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        if r["state"] == "cancelled":
            c.commit()
            return {"state": "cancelled", "duplicate": True}
        if r["state"] not in ("queued", "running", "needs_clarification"):
            raise HTTPException(409, f"Execution is {r['state']} and can no longer be cancelled")
        data = {"verdict": "BLOCKED", "execution_mode": "NONE", "evidence": [], "artifacts": [],
                "defects": ["Cancelled by the owner" + (": " + body.reason if body.reason else "")],
                "scope_verified": False, "tests_verified": False}
        c.execute("UPDATE workbench_orders SET state='cancelled',lease_until=0,result_json=?,updated_at=? WHERE id=?",
                  (json.dumps(data), int(time.time()), order_id))
        event(c, order_id, "cancelled", "owner", "Cancelled by the owner" + (": " + body.reason if body.reason else ""))
        c.commit()
        return {"state": "cancelled", "duplicate": False}
    finally:
        c.close()


class ReviseIn(BaseModel):
    kind: str
    text: str = Field(min_length=1, max_length=4000)


@router.post("/milo/executions/{order_id}/revise")
def revise_execution(order_id: str, body: ReviseIn, request: Request):
    """Owner answers a clarification question or asks for rework. The current attempt's result and artifacts are
    archived, and a new revision is queued (a new Workbench goal: nothing from the old one is reused as a PASS).
    Idempotency-Key makes a lost reply safe to repeat."""
    if body.kind not in ("rework", "clarification"):
        raise HTTPException(400, "kind must be rework or clarification")
    key = request.headers.get("Idempotency-Key", "").strip()[:200]
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        revisions = json.loads(r["revisions_json"] or "[]")
        if key and revisions and revisions[-1].get("key") == key:
            if revisions[-1].get("kind") != body.kind or revisions[-1].get("text") != body.text:
                raise HTTPException(409, "Idempotency key payload mismatch")
            c.commit()
            return {"state": r["state"], "revision": r["revision"], "duplicate": True}
        allowed_from = ("needs_clarification",) if body.kind == "clarification" else ("failed", "rejected")
        if r["state"] not in allowed_from:
            raise HTTPException(409, f"A {body.kind} needs the execution to be {' or '.join(allowed_from)}, "
                                     f"not {r['state']}")
        arts = [{"name": a["name"], "sha256": a["sha256"], "content": a["content"].decode("utf-8", "surrogateescape")}
                for a in c.execute("SELECT name,content,sha256 FROM workbench_artifacts WHERE order_id=?", (order_id,))]
        c.execute("INSERT OR REPLACE INTO workbench_history VALUES(?,?,?,?,?,?)",
                  (order_id, r["revision"] or 0, r["state"], r["result_json"], json.dumps(arts), int(time.time())))
        c.execute("DELETE FROM workbench_artifacts WHERE order_id=?", (order_id,))
        revision = (r["revision"] or 0) + 1
        revisions.append({"revision": revision, "kind": body.kind, "text": body.text, "question": r["question"],
                          "key": key or None, "at": int(time.time())})
        c.execute("""UPDATE workbench_orders SET state='queued',revision=?,revisions_json=?,result_json=NULL,
            result_digest=NULL,attempts=0,started_at=NULL,question=NULL,worker=NULL,lease_until=0,usage_json=NULL,
            updated_at=? WHERE id=?""", (revision, json.dumps(revisions), int(time.time()), order_id))
        event(c, order_id, "revised", "owner", f"{body.kind}: {body.text}", revision)
        c.commit()
        return {"state": "queued", "revision": revision, "duplicate": False}
    finally:
        c.close()


class DecisionIn(BaseModel):
    decision: str
    reason: str = ""


@router.post("/milo/executions/{order_id}/decision")
def owner_decision(order_id: str, body: DecisionIn):
    if body.decision not in ("accept", "reject"):
        raise HTTPException(400, "Expected accept or reject")
    if body.decision == "reject" and not body.reason.strip():
        raise HTTPException(400, "Rejection reason required")
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        if r["state"] == "simulated":
            raise HTTPException(409, "Simulated or sample work cannot be approved or rejected; it is a record only")
        if r["state"] != "awaiting_approval":
            raise HTTPException(409, "Execution not awaiting approval")
        report = json.loads(r["result_json"] or "{}")
        if report.get("verdict") != "PASS":
            raise HTTPException(409, "Only QA PASS may be accepted")
        if body.decision == "accept" and report.get("execution_mode") != "REAL":
            raise HTTPException(409, "Simulated work cannot be accepted as a real deliverable")
        if body.decision == "accept" and report.get("scope_verified") is not True:
            # Includes results recorded before C3: their changed files were never checked by Campus.
            raise HTTPException(409, "Campus has not verified which files this result changed; it cannot be accepted")
        if body.decision == "accept" and report.get("tests_verified") is not True:
            # Includes results recorded before the signed verification: their tests were only claimed.
            raise HTTPException(409, "Campus has not verified the test run for this result; it cannot be accepted")
        state = "completed" if body.decision == "accept" else "rejected"
        c.execute("UPDATE workbench_orders SET state=?,updated_at=? WHERE id=?", (state, int(time.time()), order_id))
        event(c, order_id, "decision", "owner", body.decision + (": " + body.reason if body.reason else ""))
        c.commit()
        return {"state": state}
    finally:
        c.close()


@router.get("/milo/executions/{order_id}/preview/{name}")
def preview_evidence(order_id: str, name: str):
    """Authenticated, non-executable preview of the exact patch and trusted QA records.
    Preview does NOT mean owner acceptance; other deliverables stay locked.
    """
    from fastapi.responses import Response
    if name not in PREVIEWABLE:
        raise HTTPException(404, "Only read-only QA evidence can be previewed")
    c = connect()
    try:
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        if r["state"] not in ("awaiting_approval", "completed", "rejected", "failed", "simulated"):
            raise HTTPException(409, "Execution has no final evidence to preview")
        a = c.execute("SELECT content,sha256 FROM workbench_artifacts WHERE order_id=? AND name=?",
                      (order_id, name)).fetchone()
        if not a:
            raise HTTPException(404, "Evidence file is unavailable")
        return Response(content=a["content"], media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                     "X-Ayven-Outcome": r["state"].upper(), "X-Content-SHA256": a["sha256"],
                     "X-Ayven-Verification": _view(r)["verification"]})
    finally:
        c.close()


@router.get("/milo/executions/{order_id}/artifacts/{name}")
def download_artifact(order_id: str, name: str):
    from fastapi.responses import Response
    c = connect()
    try:
        ensure_tables(c)
        r = c.execute("SELECT state FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        if r["state"] != "completed":
            raise HTTPException(409, "Owner acceptance required")
        a = c.execute("SELECT content,sha256 FROM workbench_artifacts WHERE order_id=? AND name=?",
            (order_id, name)).fetchone()
        if not a:
            raise HTTPException(404, "Unknown artifact")
        return Response(content=a["content"], media_type="application/octet-stream",
            headers={"X-Content-SHA256": a["sha256"], "Cache-Control": "no-store"})
    finally:
        c.close()
