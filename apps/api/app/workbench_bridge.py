"""Version 1 pull-only Campus/Workbench bridge. No execution occurs in Campus.

Campus is the authority for an order's lifecycle; see docs/contracts/workbench-execution-v1.md.

Who may move an order between states:
  owner  : create (queued), cancel (-> cancelled), revise (failed|rejected|needs_clarification -> queued, new
           revision), accept / reject (awaiting_approval -> completed | rejected) of ONE review package (bound by
           its sha256 and revision), apply (an accepted package -> the apply queue)
  worker : claim (queued -> running, lease + fence), heartbeat (keeps the lease, reports progress), result
           (running -> awaiting_approval | failed | simulated | needs_clarification)
  Campus : lease expiry (running -> queued for a new fence, or failed after MAX_ATTEMPTS), time and token
           limits (running -> failed)
A worker can only act while it holds the current fence of a live lease; anything else is a 409.

Exact-version approval: when a verified PASS arrives, Campus freezes a review package (base revision, patch
sha256, allowed files, artifact manifest, acceptance-test digests, coverage, revision) and its sha256. The owner
decides on that package by hash and revision; a decision on anything else is refused. Accepting produces an
approval signed with the attestation key (ayven.approval/v1). Applying is a separate owner action: the worker
claims the apply under its own lease and fence, Workbench checks the approval, the patch bytes and the project's
base, and applies exactly those bytes once. A rejected result is never applied.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from .db import connect
from .workbench_scope import ScopeError, normalize as normalize_scope, verify_patch
from .workbench_evidence import (acceptance_digest, attestation_key, canonical, key_id, record_of, request_key,
                                 test_evidence_problems)
from .workbench_scope import check_path

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
PREVIEWABLE = ("changes.diff", "qa-run.json", "qa-test.log", "baseline-test.log", "regression-test.log",
               "acceptance-test.log", "acceptance-baseline-test.log")
PACKAGE_SCHEMA, APPROVAL_SCHEMA = "ayven.review-package/v1", "ayven.approval/v1"
MAX_ACCEPTANCE_BYTES = 32000
FINAL_STATES = ("awaiting_approval", "completed", "rejected", "failed", "simulated", "needs_clarification",
                "cancelled")


def worker_authorized(request):
    secret = os.environ.get("AYVEN_WORKER_TOKEN", "")
    supplied = request.headers.get("Authorization", "")
    from .access import token as owner_token
    # A worker token equal to the owner's key would make every worker an owner: refused outright.
    if secret and hmac.compare_digest(secret.encode(), owner_token().encode()):
        return False
    return len(secret) >= 32 and supplied.startswith("Bearer ") and hmac.compare_digest(supplied[7:].encode(), secret.encode())


def owner_identity(request):
    """Who made an owner decision, as far as Campus can tell. The Milo server signs the owner in (Google) and
    passes the owner's address in X-Ayven-Owner over its bearer-authenticated call; Campus records both."""
    supplied = request.headers.get("Authorization", "")
    principal = "bearer" if supplied.startswith("Bearer ") else "session" if request.cookies.get("ayven_session") \
        else "local"
    asserted = request.headers.get("X-Ayven-Owner", "").strip()[:200]
    return {"principal": principal, "owner": asserted or None}


def require_worker(request):
    if not worker_authorized(request):
        raise HTTPException(401, "Worker credential required")


COLUMNS = {   # added after the first release; NULL/defaults for older rows
    "allowed_json": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 0", "revisions_json": "TEXT NOT NULL DEFAULT '[]'",
    "max_minutes": "INTEGER", "token_cap": "INTEGER", "allow_clarification": "INTEGER NOT NULL DEFAULT 0",
    "started_at": "INTEGER", "usage_json": "TEXT", "question": "TEXT", "updated_at": "INTEGER",
    "acceptance_json": "TEXT", "superseded_json": "TEXT", "removals_json": "TEXT", "package_json": "TEXT",
    "package_sha256": "TEXT", "decision_json": "TEXT", "claimed_at": "INTEGER", "heartbeat_at": "INTEGER",
    "finished_at": "INTEGER",
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
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_decisions (
        order_id TEXT NOT NULL, seq INTEGER NOT NULL, revision INTEGER NOT NULL, decision TEXT NOT NULL,
        reason TEXT NOT NULL, package_sha256 TEXT NOT NULL, owner_json TEXT NOT NULL, approval_json TEXT,
        at INTEGER NOT NULL, PRIMARY KEY(order_id,seq))""")
    c.execute("""CREATE TABLE IF NOT EXISTS workbench_applies (
        order_id TEXT NOT NULL, revision INTEGER NOT NULL, approval_id TEXT NOT NULL, package_sha256 TEXT NOT NULL,
        state TEXT NOT NULL, worker TEXT, fence INTEGER NOT NULL DEFAULT 0, lease_until INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0, requested_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        outcome_json TEXT, PRIMARY KEY(order_id,revision))""")
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


class AcceptanceTestIn(BaseModel):
    path: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=MAX_ACCEPTANCE_BYTES)


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
    # Owner-written tests the worker never sees; Workbench runs them on the patched code (verification v2).
    acceptance_tests: list[AcceptanceTestIn] = Field(default_factory=list, max_length=10)
    # Original tests the owner says this change may retire, and public definitions it may remove ("path::name").
    superseded_tests: list[str] = Field(default_factory=list, max_length=50)
    allowed_removals: list[str] = Field(default_factory=list, max_length=50)


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
    for name, default in (("max_minutes", DEFAULT_MINUTES), ("token_cap", None), ("allow_clarification", False),
                          ("acceptance_tests", []), ("superseded_tests", []), ("allowed_removals", [])):
        if payload.get(name) == default:
            payload.pop(name, None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _check_acceptance(body, allowed):
    """Owner acceptance tests: Python test files at fresh exact paths the worker may not change."""
    total, seen = 0, set()
    for test in body.acceptance_tests:
        try:
            path = check_path(test.path)
        except ScopeError as exc:
            raise HTTPException(400, f"Acceptance test path: {exc}")
        name = path.rsplit("/", 1)[-1]
        in_tests_dir = bool({"tests", "test"} & set(path.split("/")[:-1]))
        if not path.endswith(".py") or name in ("conftest.py", "__init__.py") or \
                not (name.startswith("test_") or name.endswith("_test.py") or in_tests_dir):
            raise HTTPException(400, f"Acceptance test {path} must be a Python test file (test_*.py or under tests/)")
        if path.casefold() in {a.casefold() for a in allowed}:
            raise HTTPException(400, f"Acceptance test {path} is on the allowed-files list, so the worker could change it")
        if path.casefold() in seen:
            raise HTTPException(400, "Acceptance test paths must be distinct")
        seen.add(path.casefold())
        total += len(test.content.encode())
    if total > MAX_ACCEPTANCE_BYTES:
        raise HTTPException(400, f"Acceptance tests total {total} bytes, over the {MAX_ACCEPTANCE_BYTES}-byte limit")
    for name in body.superseded_tests + body.allowed_removals:
        if not name.strip() or len(name) > 300 or not name.isprintable():
            raise HTTPException(400, "superseded_tests and allowed_removals take short printable names")


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
    _check_acceptance(body, allowed)
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
               created_at,allowed_json,max_minutes,token_cap,allow_clarification,updated_at,acceptance_json,
               superseded_json,removals_json)
              VALUES(?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)""",
              (key, result["project_id"], body.objective, body.project_alias, json.dumps(body.acceptance_criteria),
               json.dumps(body.constraints), body.job_cap_gbp, fingerprint, now, json.dumps(allowed),
               body.max_minutes, body.token_cap, int(body.allow_clarification), now,
               json.dumps([t.model_dump() for t in body.acceptance_tests]),
               json.dumps(sorted(set(body.superseded_tests))), json.dumps(sorted(set(body.allowed_removals)))))
        except sqlite3.IntegrityError:
            c.rollback()
            c.close()
            c = connect()
            existing = c.execute("SELECT * FROM workbench_orders WHERE id=?", (key,)).fetchone()
            if existing:
                return _duplicate(existing, fingerprint, key)
            raise HTTPException(409, "Order conflicts with an existing project")
        event(c, key, "created", "owner", f"Order created; allowed files: {', '.join(allowed)}; "
              f"limits: {body.max_minutes} min" + (f", {body.token_cap} tokens" if body.token_cap else "") +
              (f"; {len(body.acceptance_tests)} owner acceptance test file(s)" if body.acceptance_tests else ""), 0)
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
            started_at=COALESCE(started_at,?),claimed_at=?,heartbeat_at=?,updated_at=? WHERE id=?""",
            (body.worker_id, fence, now + lease_seconds(), now, now, now, now, r["id"]))
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
           "token_cap": r["token_cap"], "allow_clarification": bool(r["allow_clarification"]),
           "acceptance_tests": _loads(r["acceptance_json"]), "superseded_tests": _loads(r["superseded_json"]),
           "allowed_removals": _loads(r["removals_json"])}}
    finally:
        c.close()


def _loads(text, default=None):
    return json.loads(text) if text else ([] if default is None else default)


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
        c.execute("UPDATE workbench_orders SET lease_until=?,heartbeat_at=?,updated_at=? WHERE id=?",
                  (now + lease_seconds(), now, now, order_id))
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
            tests = test_evidence_problems(files, order_id=order_id, revision=r["revision"] or 0, allowed=allowed,
                                           acceptance=_loads(r["acceptance_json"]),
                                           superseded=_loads(r["superseded_json"]),
                                           allowed_removals=_loads(r["removals_json"]))
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
        package, package_sha = (None, None)
        if state == "awaiting_approval":
            package, package_sha = review_package(r, {n: raw for n, raw, _ in artifacts})
            result_data["coverage"] = package["coverage"]
            result_data["package_sha256"] = package_sha
        usage = body.usage.model_dump() if body.usage else None
        # The digest is of what the worker sent, so an identical replay stays idempotent after a downgrade.
        c.execute("""UPDATE workbench_orders SET state=?,result_json=?,result_digest=?,lease_until=0,question=?,
            usage_json=COALESCE(?,usage_json),updated_at=?,finished_at=?,package_json=?,package_sha256=?,
            decision_json=NULL WHERE id=?""",
            (state, json.dumps(result_data), digest, body.question if state == "needs_clarification" else None,
             json.dumps(usage) if usage else None, int(time.time()), int(time.time()),
             json.dumps(package) if package else None, package_sha, order_id))
        event(c, order_id, "result", "worker", f"{body.verdict} ({body.execution_mode}) -> {state}" +
              (": " + "; ".join(result_data.get("defects") or [])[:600] if result_data.get("defects") else "") +
              (": " + body.question if state == "needs_clarification" else ""))
        c.commit()
        return {"ok": True, "state": state, "owner_accepted": False,
                "campus_rejected": bool(result_data.get("campus_rejected"))}
    finally:
        c.close()


def review_package(r, files):
    """The exact thing the owner decides on. Frozen when the verified PASS arrives; its sha256 is what an owner
    decision, the signed approval and the apply all name."""
    record = record_of(files)
    envelope = json.loads(files["qa-run.json"].decode("utf-8"))
    package = {
        "schema": PACKAGE_SCHEMA, "order_id": r["id"], "revision": r["revision"] or 0,
        "request_key": request_key(r["id"], r["revision"] or 0), "project_alias": r["project_alias"],
        "objective": r["objective"], "acceptance_criteria": _loads(r["criteria_json"]),
        "allowed_files": _loads(r["allowed_json"]), "base_revision": record.get("base_revision"),
        "patch_sha256": record.get("patch_sha256"), "patch_bytes": record.get("patch_bytes"),
        "changed_files": record.get("changed_files"), "execution_mode": "REAL",
        "artifacts": {n: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for n, raw in sorted(files.items())},
        "verification": {"schema": record.get("schema"), "verdict": record.get("verdict"),
                         "record_sha256": hashlib.sha256(canonical(record)).hexdigest(),
                         "key_id": (envelope.get("signature") or {}).get("key_id")},
        "acceptance_tests": record.get("acceptance_tests"), "superseded_tests": record.get("superseded_tests"),
        "allowed_removals": record.get("allowed_removals"), "removed_definitions": record.get("removed_definitions"),
        "signature_changes": record.get("signature_changes"), "tests_modified": record.get("tests_modified"),
        "coverage": record.get("coverage"),
    }
    return package, hashlib.sha256(canonical(package)).hexdigest()


def _intact(c, order_id, package):
    """Are the stored artifacts still byte-for-byte the ones in the package? (Refuses a changed database row.)"""
    stored = {a["name"]: a["content"] for a in
              c.execute("SELECT name,content FROM workbench_artifacts WHERE order_id=?", (order_id,)).fetchall()}
    want = package.get("artifacts") or {}
    return set(stored) == set(want) and all(
        hashlib.sha256(stored[n]).hexdigest() == m["sha256"] and len(stored[n]) == m["bytes"] for n, m in want.items())


def _apply_view(c, r):
    if c is None:
        return None
    a = c.execute("SELECT * FROM workbench_applies WHERE order_id=? AND revision=?",
                  (r["id"], r["revision"] or 0)).fetchone()
    if not a:
        return None
    return {"state": a["state"], "attempts": a["attempts"], "requested_at": a["requested_at"],
            "updated_at": a["updated_at"], "approval_id": a["approval_id"], "package_sha256": a["package_sha256"],
            "outcome": _loads(a["outcome_json"], {}) or None}


def _view(r, c=None):
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
            "result": report, "owner_accepted": r["state"] == "completed",
            "acceptance_criteria": _loads(r["criteria_json"]), "constraints": _loads(r["constraints_json"]),
            "job_cap_gbp": r["budget_gbp"],
            "acceptance_tests": [{"path": t["path"]} for t in _loads(r["acceptance_json"])] + [],
            "superseded_tests": _loads(r["superseded_json"]), "allowed_removals": _loads(r["removals_json"]),
            "worker_id": r["worker"], "claimed_at": r["claimed_at"], "last_heartbeat_at": r["heartbeat_at"],
            "lease_until": r["lease_until"] or None, "finished_at": r["finished_at"],
            "review": {"package_sha256": r["package_sha256"],
                       "coverage": (_loads(r["package_json"], {}) or {}).get("coverage")} if r["package_sha256"] else None,
            "decision": _decision_public(r["decision_json"]),
            "apply": _apply_view(c, r)}


def _decision_public(text):
    d = _loads(text, {})
    if not d:
        return None
    return {k: d.get(k) for k in ("decision", "reason", "owner", "decided_at", "package_sha256", "revision",
                                  "approval_id", "acknowledged_coverage")}


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
                "updated_at": r["updated_at"], "finished_at": r["finished_at"], "worker_id": r["worker"],
                "last_heartbeat_at": r["heartbeat_at"], "acceptance_criteria": v["acceptance_criteria"],
                "constraints": v["constraints"], "job_cap_gbp": r["budget_gbp"], "review": v["review"],
                "decision": v["decision"], "apply": _apply_view(c, r),
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
        return _view(r, c)
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
    return _revise(order_id, body, request.headers.get("Idempotency-Key", "").strip()[:200])


class ClarificationIn(BaseModel):
    answer: str = Field(default="", max_length=4000)


@router.post("/milo/executions/{order_id}/clarification")
def answer_clarification(order_id: str, body: ClarificationIn, request: Request):
    """Milo's clarification route (docs/integration/milo-connected-journey.md, D1): the same order resumes as a new
    revision whose brief carries the question and this answer. 400 empty answer; 409 not waiting for an answer."""
    if not body.answer.strip():
        raise HTTPException(400, "Write an answer")
    return _revise(order_id, ReviseIn(kind="clarification", text=body.answer.strip()),
                   request.headers.get("Idempotency-Key", "").strip()[:200])


def _revise(order_id, body, key):
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
            package_json=NULL,package_sha256=NULL,decision_json=NULL,claimed_at=NULL,heartbeat_at=NULL,
            finished_at=NULL,updated_at=? WHERE id=?""", (revision, json.dumps(revisions), int(time.time()), order_id))
        event(c, order_id, "revised", "owner", f"{body.kind}: {body.text}", revision)
        c.commit()
        return {"state": "queued", "revision": revision, "duplicate": False}
    finally:
        c.close()


class DecisionIn(BaseModel):
    decision: str
    reason: str = Field(default="", max_length=2000)
    # The exact review package the owner looked at (GET .../review) and its revision. Required: a decision is
    # about one frozen version, never "whatever the result is now".
    package_sha256: str = Field(default="", max_length=64)
    revision: int | None = None
    # Required to accept when coverage.level is "worker-tests-only" (no original or owner tests ran).
    acknowledge_coverage: bool = False


@router.get("/milo/executions/{order_id}/review")
def review_package_view(order_id: str):
    """The exact review package: what was verified, on which base, with which patch hash, what the tests did and
    did not cover. Decide by quoting package_sha256 and revision."""
    c = connect()
    try:
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        if not r["package_sha256"]:
            raise HTTPException(409, f"Execution is {r['state']}: there is no verified result to review")
        package = json.loads(r["package_json"])
        return {"order_id": order_id, "state": r["state"], "revision": r["revision"] or 0,
                "package_sha256": r["package_sha256"], "package": package, "intact": _intact(c, order_id, package),
                "decision": _decision_public(r["decision_json"]), "apply": _apply_view(c, r)}
    finally:
        c.close()


@router.post("/milo/executions/{order_id}/decision")
def owner_decision(order_id: str, body: DecisionIn, request: Request):
    if body.decision not in ("accept", "reject"):
        raise HTTPException(400, "Expected accept or reject")
    if body.decision == "reject" and not body.reason.strip():
        raise HTTPException(400, "Rejection reason required")
    if not body.package_sha256 or body.revision is None:
        raise HTTPException(400, "package_sha256 and revision are required: decide on the review package you read "
                                 "(GET /milo/executions/{id}/review)")
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        prior = _loads(r["decision_json"], {})
        if prior:
            # Idempotent: the same decision on the same package is a duplicate; anything else is refused.
            if prior.get("decision") == body.decision and prior.get("package_sha256") == body.package_sha256 and \
                    prior.get("revision") == body.revision:
                c.commit()
                return {"state": r["state"], "decision": prior["decision"], "reason": prior.get("reason"),
                        "approval_id": prior.get("approval_id"), "package_sha256": prior["package_sha256"],
                        "duplicate": True}
            raise HTTPException(409, f"This result was already {prior.get('decision')}ed; a decision cannot be changed")
        if r["state"] == "simulated":
            raise HTTPException(409, "Simulated or sample work cannot be approved or rejected; it is a record only")
        if r["state"] != "awaiting_approval":
            raise HTTPException(409, "Execution not awaiting approval")
        if body.revision != (r["revision"] or 0):
            raise HTTPException(409, f"You reviewed revision {body.revision}; the execution is at revision "
                                     f"{r['revision'] or 0}. Review the current result.")
        if not r["package_sha256"]:
            # Includes results recorded before exact-version review, or never verified by Campus.
            raise HTTPException(409, "This result has no verified review package (its files and test run were not "
                                     "verified by Campus); it cannot be decided on as a deliverable")
        if not hmac.compare_digest(body.package_sha256, r["package_sha256"]):
            raise HTTPException(409, "The result is not the version you reviewed (review package hash differs). "
                                     "Review it again.")
        package = json.loads(r["package_json"])
        if not _intact(c, order_id, package):
            raise HTTPException(409, "Stored artifacts no longer match the review package; nothing can be decided")
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
        level = (package.get("coverage") or {}).get("level")
        if body.decision == "accept" and level == "worker-tests-only" and not body.acknowledge_coverage:
            raise HTTPException(409, "Only tests the worker wrote ran on this change (no original or owner tests). "
                                     "Accept only with acknowledge_coverage=true, or ask for rework with acceptance tests.")
        now = int(time.time())
        owner = owner_identity(request)
        decision = {"decision": body.decision, "reason": body.reason.strip(), "owner": owner, "decided_at": now,
                    "package_sha256": r["package_sha256"], "revision": r["revision"] or 0,
                    "acknowledged_coverage": bool(body.acknowledge_coverage), "approval_id": None, "approval": None}
        if body.decision == "accept":
            key = attestation_key()
            if not key:
                raise HTTPException(503, "Campus has no attestation key; it cannot sign an approval")
            approval = {"schema": APPROVAL_SCHEMA, "approval_id": uuid.uuid4().hex, "decision": "accept",
                        "order_id": order_id, "revision": r["revision"] or 0,
                        "request_key": request_key(order_id, r["revision"] or 0),
                        "package_sha256": r["package_sha256"], "patch_sha256": package["patch_sha256"],
                        "base_revision": package["base_revision"], "allowed_files": package["allowed_files"],
                        "project_alias": package["project_alias"], "owner": owner, "decided_at": now}
            decision["approval_id"] = approval["approval_id"]
            decision["approval"] = {"record": approval, "signature": {
                "alg": "HMAC-SHA256", "key_id": key_id(key),
                "value": hmac.new(key, canonical(approval), hashlib.sha256).hexdigest()}}
        state = "completed" if body.decision == "accept" else "rejected"
        c.execute("UPDATE workbench_orders SET state=?,decision_json=?,updated_at=? WHERE id=?",
                  (state, json.dumps(decision), now, order_id))
        seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM workbench_decisions WHERE order_id=?", (order_id,)).fetchone()[0]
        c.execute("INSERT INTO workbench_decisions VALUES(?,?,?,?,?,?,?,?,?)",
                  (order_id, seq, r["revision"] or 0, body.decision, decision["reason"], r["package_sha256"],
                   json.dumps(owner), json.dumps(decision["approval"]) if decision["approval"] else None, now))
        event(c, order_id, "decision", "owner", f"{body.decision} of package {r['package_sha256'][:16]} "
              f"(revision {r['revision'] or 0}) by {owner.get('owner') or owner['principal']}" +
              (": " + decision["reason"] if decision["reason"] else ""))
        c.commit()
        return {"state": state, "decision": body.decision, "reason": decision["reason"],
                "approval_id": decision["approval_id"], "package_sha256": r["package_sha256"], "duplicate": False}
    finally:
        c.close()


class ApplyIn(BaseModel):
    package_sha256: str = Field(min_length=64, max_length=64)


@router.post("/milo/executions/{order_id}/apply")
def request_apply(order_id: str, body: ApplyIn):
    """Owner asks for an accepted package to be applied to the project on the Workbench host. Never automatic.
    Idempotent: a repeat while queued or after success reports the same apply; after a refusal it re-queues."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        decision = _loads(r["decision_json"], {})
        if r["state"] == "rejected" or decision.get("decision") == "reject":
            raise HTTPException(409, "A rejected result is never applied")
        if r["state"] != "completed" or decision.get("decision") != "accept" or not decision.get("approval"):
            raise HTTPException(409, "Accept the result before applying it")
        if not hmac.compare_digest(body.package_sha256, decision["package_sha256"]):
            raise HTTPException(409, "That is not the package you accepted")
        if not _intact(c, order_id, json.loads(r["package_json"])):
            raise HTTPException(409, "Stored artifacts no longer match the accepted package; it cannot be applied")
        now = int(time.time())
        a = c.execute("SELECT * FROM workbench_applies WHERE order_id=? AND revision=?",
                      (order_id, r["revision"] or 0)).fetchone()
        if a and a["state"] in ("requested", "claimed", "applied"):
            c.commit()
            return {"apply_state": a["state"], "duplicate": True, "outcome": _loads(a["outcome_json"], {}) or None}
        if a:
            c.execute("UPDATE workbench_applies SET state='requested',attempts=0,lease_until=0,worker=NULL,"
                      "updated_at=? WHERE order_id=? AND revision=?", (now, order_id, r["revision"] or 0))
        else:
            c.execute("INSERT INTO workbench_applies (order_id,revision,approval_id,package_sha256,state,"
                      "requested_at,updated_at) VALUES(?,?,?,?,'requested',?,?)",
                      (order_id, r["revision"] or 0, decision["approval_id"], decision["package_sha256"], now, now))
        event(c, order_id, "apply_requested", "owner", f"Apply requested for package {decision['package_sha256'][:16]}")
        c.commit()
        return {"apply_state": "requested", "duplicate": False}
    finally:
        c.close()


@router.get("/milo/executions/{order_id}/apply")
def apply_status(order_id: str):
    c = connect()
    try:
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown execution")
        return {"order_id": order_id, "apply": _apply_view(c, r)}
    finally:
        c.close()


@router.post("/internal/workbench/apply/claim")
def claim_apply(body: ClaimIn, request: Request):
    require_worker(request)
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        now = int(time.time())
        for a in c.execute("SELECT * FROM workbench_applies WHERE state='claimed' AND lease_until<?", (now,)).fetchall():
            failed = a["attempts"] >= MAX_ATTEMPTS
            c.execute("UPDATE workbench_applies SET state=?,lease_until=0,updated_at=?,outcome_json=? "
                      "WHERE order_id=? AND revision=?",
                      ("failed" if failed else "requested", now,
                       json.dumps({"outcome": "failed", "detail": "Worker lease expired"}) if failed else a["outcome_json"],
                       a["order_id"], a["revision"]))
            event(c, a["order_id"], "apply_lease_expired", "campus",
                  "Apply worker stopped heartbeating" + ("; apply failed" if failed else "; re-queued"))
        a = c.execute("SELECT * FROM workbench_applies WHERE state='requested' ORDER BY requested_at LIMIT 1").fetchone()
        if not a:
            c.commit()
            return {"apply": None}
        r = c.execute("SELECT * FROM workbench_orders WHERE id=?", (a["order_id"],)).fetchone()
        decision = _loads(r["decision_json"], {}) if r else {}
        files = {x["name"]: x["content"] for x in
                 c.execute("SELECT name,content FROM workbench_artifacts WHERE order_id=?", (a["order_id"],)).fetchall()}
        if not r or r["state"] != "completed" or decision.get("approval_id") != a["approval_id"] or \
                not _intact(c, a["order_id"], json.loads(r["package_json"])):
            c.execute("UPDATE workbench_applies SET state='refused',updated_at=?,outcome_json=? WHERE order_id=? AND revision=?",
                      (now, json.dumps({"outcome": "refused", "detail": "The accepted package no longer matches"}),
                       a["order_id"], a["revision"]))
            event(c, a["order_id"], "apply_refused", "campus", "The accepted package no longer matches; not applied")
            c.commit()
            return {"apply": None}
        fence = a["fence"] + 1
        c.execute("UPDATE workbench_applies SET state='claimed',worker=?,fence=?,lease_until=?,attempts=attempts+1,"
                  "updated_at=? WHERE order_id=? AND revision=?",
                  (body.worker_id, fence, now + lease_seconds(), now, a["order_id"], a["revision"]))
        event(c, a["order_id"], "apply_claimed", "worker", f"Apply claimed by {body.worker_id} (fence {fence})")
        c.commit()
        return {"apply": {"order_id": a["order_id"], "revision": a["revision"], "fence": fence,
                          "lease_seconds": lease_seconds(), "project_alias": r["project_alias"],
                          "approval": decision["approval"], "package_sha256": a["package_sha256"],
                          "patch": files["changes.diff"].decode("utf-8", "surrogateescape"),
                          "record": json.loads(files["qa-run.json"].decode("utf-8"))}}
    finally:
        c.close()


class ApplyResultIn(LeaseIn):
    outcome: str
    commit: str | None = Field(default=None, max_length=40)
    detail: str = Field(default="", max_length=2000)


@router.post("/internal/workbench/apply/{order_id}/result")
def apply_result(order_id: str, body: ApplyResultIn, request: Request):
    require_worker(request)
    if body.outcome not in ("applied", "refused"):
        raise HTTPException(400, "outcome must be applied or refused")
    if body.outcome == "applied" and not (body.commit and len(body.commit) == 40 and
                                          all(ch in "0123456789abcdef" for ch in body.commit)):
        raise HTTPException(400, "An applied result names the 40-character commit it created")
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        a = c.execute("SELECT * FROM workbench_applies WHERE order_id=? ORDER BY revision DESC LIMIT 1",
                      (order_id,)).fetchone()
        if not a:
            raise HTTPException(404, "No apply for this order")
        outcome = {"outcome": body.outcome, "commit": body.commit, "detail": body.detail, "worker": body.worker_id}
        if a["state"] in ("applied", "refused", "failed"):
            if a["worker"] == body.worker_id and a["fence"] == body.fence and _loads(a["outcome_json"], {}) == outcome:
                c.commit()
                return {"ok": True, "duplicate": True, "apply_state": a["state"]}
            raise HTTPException(409, "Apply already finished")
        if a["state"] != "claimed" or a["worker"] != body.worker_id or a["fence"] != body.fence or \
                a["lease_until"] < int(time.time()):
            raise HTTPException(409, "Stale apply lease")
        state = "applied" if body.outcome == "applied" else "refused"
        c.execute("UPDATE workbench_applies SET state=?,lease_until=0,updated_at=?,outcome_json=? "
                  "WHERE order_id=? AND revision=?", (state, int(time.time()), json.dumps(outcome), order_id, a["revision"]))
        event(c, order_id, "applied" if state == "applied" else "apply_refused", "worker",
              (f"Applied as commit {body.commit}" if state == "applied" else "Workbench refused to apply") +
              (": " + body.detail if body.detail else ""))
        c.commit()
        return {"ok": True, "duplicate": False, "apply_state": state}
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
