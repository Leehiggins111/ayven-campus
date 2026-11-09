"""Prototype orders and Campus review (first release, 9 Nov 2026).

Ayven plans; Lee approves the plan; Workbench builds and tests it; **Campus reviews the actual prototype**; Milo
relays the review and the prototype. This module is the Campus part:

  POST /ayven/prototypes                      owner: queue a build of an APPROVED plan revision (idempotent per
                                              job + revision). Codex's Ayven lifecycle calls this after Lee
                                              approves; Campus refuses a plan whose approval does not name it.
  (worker)  /internal/workbench/claim|heartbeat|result   the existing pull protocol; prototype orders reuse the
                                              same leases, fences, expiry and cancellation (workbench_bridge).
  GET  /ayven/prototypes/{order_id}           owner: state, build checks, review findings (passed / failed),
                                              the private prototype link, and the pending decision if any.
  GET  /ayven/prototypes/{order_id}/prototype/{index.html|style.css|app.js}
                                              owner-only, CSP-locked (no network) once the review has run.
  POST /ayven/prototypes/{order_id}/review    owner: (re)run the review if it did not complete (idempotent).
  POST /ayven/prototypes/{order_id}/decision  owner: after a FAILED review or build, Lee decides: {"decision":
                                              "fix"} queues ONE fix build with the findings, {"decision":
                                              "no_fix"} closes it. Nothing is rebuilt until Lee decides.

The review is independent of Workbench's own tests: Campus (1) verifies the Workbench signature over the
ayven.prototype/v1 record, (2) checks every returned file's bytes against the signed hashes and the plan digest
against the approved one, (3) labels execution honestly (REAL only from verified Ollama provenance; FIXTURE and
SIMULATED are never presented as real work), and (4) re-runs the approved journeys AND the reviewer's held-out
checks (plan.review.checks, which are not sent to Workbench) in its own fresh, network-blocked Chromium.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .db import connect
from .workbench_evidence import attestation_key, canonical, key_id

router = APIRouter()

PLAN_SCHEMA = "ayven.plan/v1"
RECORD_SCHEMA = "ayven.prototype/v1"
RECORD = "prototype-record.json"
FILES = ("index.html", "style.css", "app.js")
WIDTHS = (1280, 390)
KIND = "prototype"
REVIEW_TEAM = os.environ.get("AYVEN_PROTOTYPE_REVIEW_TEAM", "labs")
RUNNER = Path(__file__).resolve().parent / "intelligence" / "web_browser_runner.py"
CSP = ("default-src 'self'; connect-src 'none'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
       "form-action 'none'; base-uri 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'self'")
TYPES = {"index.html": "text/html; charset=utf-8", "style.css": "text/css; charset=utf-8",
         "app.js": "text/javascript; charset=utf-8"}
# Prototype order states (stored in workbench_orders.state):
#   queued -> running -> reviewing -> review_passed            (Lee may open and check it)
#                     -> reviewing -> review_failed            (waits for Lee: fix / no_fix)
#                     -> failed (build failed or rejected)     (waits for Lee: fix / no_fix)
#   review_failed|failed --fix--> fix_requested (+ new queued fix order)   --no_fix--> closed
DECIDABLE = ("review_failed", "failed")
PROTOTYPE_FINAL = ("review_passed", "review_failed", "failed", "fix_requested", "closed", "cancelled")
_REVIEWS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def plan_sha256(plan) -> str:
    return hashlib.sha256(canonical(plan)).hexdigest()


def build_plan(plan: dict) -> dict:
    """What Workbench is sent: the approved plan without the reviewer's held-out checks."""
    return {k: v for k, v in plan.items() if k != "review"}


class ApprovalIn(BaseModel):
    approval_id: str = Field(min_length=1, max_length=200)
    plan_id: str
    revision: int
    plan_sha256: str
    decision: str
    approved_by: str = Field(default="", max_length=200)
    approved_at: str = Field(default="", max_length=60)


class PrototypeIn(BaseModel):
    plan: dict
    approval: ApprovalIn
    max_minutes: int = Field(default=30, ge=1, le=240)


class DecisionIn(BaseModel):
    decision: str
    note: str = Field(default="", max_length=2000)


def _validate(plan: dict, approval: ApprovalIn) -> str:
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise HTTPException(400, "Expected an ayven.plan/v1 plan")
    for field in ("plan_id", "job_id", "title", "brief"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            raise HTTPException(400, f"Plan has no {field}")
    if not isinstance(plan.get("revision"), int) or plan["revision"] < 1:
        raise HTTPException(400, "Plan revision must be a positive integer")
    journeys = (plan.get("prototype") or {}).get("journeys")
    if (plan.get("prototype") or {}).get("kind") != "offline-browser" or not isinstance(journeys, list) or not journeys:
        raise HTTPException(400, "Plan has no offline-browser acceptance journeys")
    digest = plan_sha256(plan)
    if approval.decision != "approved":
        raise HTTPException(409, "Plan is not approved; Workbench does not start before Lee approves the plan")
    if approval.plan_sha256 != digest or approval.plan_id != plan["plan_id"] or approval.revision != plan["revision"]:
        raise HTTPException(409, "Approval is for a different plan or revision")
    return digest


def plan_key(plan: dict) -> str:
    return f"{plan['job_id']}:plan-rev-{plan['revision']}"


def _insert(c, order_id, key, plan, digest, approval: dict, max_minutes, fix_of=None, findings=None):
    from .workbench_bridge import event
    now = int(time.time())
    requirements = [j.get("requirement", "") for j in plan["prototype"]["journeys"]]
    revisions = [{"kind": "review_findings", "text": "\n".join(findings or []), "fix_of": fix_of}] if fix_of else []
    c.execute("""INSERT INTO workbench_orders
        (id,project_id,objective,project_alias,criteria_json,constraints_json,budget_gbp,state,request_digest,
         created_at,allowed_json,max_minutes,allow_clarification,updated_at,kind,plan_json,plan_sha256,
         approval_json,plan_key,fix_of,revisions_json)
        VALUES(?,?,?,?,?,?,0,'queued',?,?,?,?,0,?,?,?,?,?,?,?,?)""",
              (order_id, "proto-" + hashlib.sha256(order_id.encode()).hexdigest()[:20], plan["brief"], "prototype",
               json.dumps(requirements), "[]", digest, now, json.dumps(list(FILES)), max_minutes, now, KIND,
               json.dumps(plan), digest, json.dumps(approval), key, fix_of, json.dumps(revisions)))
    event(c, order_id, "created", "owner", (f"Fix build of {fix_of} queued after Lee's decision" if fix_of else
                                            f"Prototype build queued for approved plan {plan['plan_id']} "
                                            f"revision {plan['revision']} (approval {approval['approval_id']})"), 0)


@router.post("/ayven/prototypes")
def enqueue(body: PrototypeIn, request: Request):
    from .ayven_os import is_read_only
    from .workbench_bridge import ensure_tables
    if is_read_only(request):
        raise HTTPException(403, "Read-only credential cannot queue builds")
    if str(body.plan.get("job_id", "")).startswith("ayven-plan-"):
        raise HTTPException(409, "Use the recorded plan decision to queue this job")
    digest = _validate(body.plan, body.approval)
    key = plan_key(body.plan)
    order_id = "proto-" + hashlib.sha256(key.encode()).hexdigest()[:16]
    approval = body.approval.model_dump()
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        existing = c.execute("SELECT state,plan_sha256 FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if existing:
            c.commit()
            if existing["plan_sha256"] != digest:
                raise HTTPException(409, "This job revision was already queued with a different plan")
            return {"order_id": order_id, "duplicate": True, "state": existing["state"], "request_key": key}
        _insert(c, order_id, key, body.plan, digest, approval, body.max_minutes)
        c.commit()
    finally:
        c.close()
    from . import teams
    # Planning ends when Lee approves the plan: free the planning team for the next job (no-op if none held).
    teams.release(body.plan["job_id"], "planning", teams.PLANNING_TEAM)
    return {"order_id": order_id, "duplicate": False, "state": "queued", "request_key": key}


def claim_fields(r) -> dict:
    """Extra claim fields for a prototype order (workbench_bridge.claim)."""
    plan = json.loads(r["plan_json"])
    out = {"kind": KIND, "plan": build_plan(plan), "plan_sha256": plan_sha256(build_plan(plan)),
           "approval": dict(json.loads(r["approval_json"]), plan_sha256=plan_sha256(build_plan(plan))),
           "approved_plan_sha256": r["plan_sha256"], "request_key": r["plan_key"]}
    revisions = json.loads(r["revisions_json"] or "[]")
    if revisions:
        out["review_findings"] = revisions[-1].get("text", "")
    return out


# ---------------------------------------------------------------- result verification (worker -> Campus)

def signed_prototype(files: dict, key=None):
    key = key if key is not None else attestation_key()
    raw = files.get(RECORD)
    if not key or raw is None:
        return None
    try:
        envelope = json.loads(raw.decode("utf-8"))
        record, signature = envelope["record"], envelope.get("signature") or {}
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not isinstance(record, dict) or not isinstance(signature, dict) or not isinstance(signature.get("value"), str):
        return None
    expected = hmac.new(key, canonical(record), hashlib.sha256).hexdigest()
    if signature.get("key_id") != key_id(key) or not hmac.compare_digest(signature["value"], expected):
        return None
    return record


def integrity_problems(r, files: dict, record) -> list[str]:
    plan = json.loads(r["plan_json"])
    sent = plan_sha256(build_plan(plan))
    problems = []
    if record is None:
        return ["The prototype record is missing or its Workbench signature does not verify"]
    if record.get("schema") != RECORD_SCHEMA:
        problems.append("The record is not an ayven.prototype/v1 record")
    if record.get("plan_sha256") != sent:
        problems.append("The prototype was built from a different plan than the one Lee approved")
    if record.get("request_key") != r["plan_key"]:
        problems.append("The record belongs to a different build request")
    hashes = record.get("files") or {}
    for name in FILES:
        if name not in files:
            problems.append(f"{name} was not returned")
        elif hashlib.sha256(files[name]).hexdigest() != hashes.get(name):
            problems.append(f"{name} does not match the file Workbench tested")
    return problems


def mode_of(record) -> str:
    mode = (record or {}).get("execution_mode")
    if mode == "FIXTURE":
        return "FIXTURE"
    prov = (record or {}).get("model_provenance") or {}
    return "REAL" if prov.get("kind") == "ollama" and not prov.get("problems") else "SIMULATED"


def accept_result(c, r, body, artifacts) -> tuple[str, dict]:
    """(state, result_data) for a prototype order's result. Called inside workbench_bridge.result's transaction."""
    files = {n: raw for n, raw, _ in artifacts}
    record = signed_prototype(files)
    data = {"verdict": body.verdict, "worker_execution_mode": body.execution_mode, "evidence": body.evidence,
            "defects": body.defects, "artifacts": [{"name": n, "sha256": h} for n, _, h in artifacts],
            "kind": KIND, "build": (record or {}).get("tests"), "build_id": (record or {}).get("build_id"),
            "model_provenance": (record or {}).get("model_provenance")}
    data["execution_mode"] = mode_of(record) if record else "NONE"
    if body.verdict != "PASS":
        data["findings"] = {"passed": [e for e in body.evidence if e.endswith(": passed")],
                            "failed": list(body.defects) or ["Workbench did not produce a passing prototype"],
                            "stage": "build"}
        return "failed", data
    problems = integrity_problems(r, files, record)
    if problems or (record or {}).get("verdict") != "PASS":
        data.update(verdict="FAIL", campus_rejected=True,
                    findings={"passed": [], "failed": problems or ["Signed record does not say PASS"],
                              "stage": "integrity"})
        return "failed", data
    return "reviewing", data


# ---------------------------------------------------------------- the independent review

def run_journeys(files: dict, journeys: list, *, mutant=False) -> dict:
    """Campus's own runner (intelligence/web_browser_runner.py) in its own interpreter."""
    if os.environ.get("AYVEN_ALLOW_BROWSER_BUILD", "0") != "1" and os.environ.get("AYVEN_ALLOW_CODE", "0") != "1":
        return {"passed": False, "status": "disabled", "error": "Browser review is disabled (AYVEN_ALLOW_BROWSER_BUILD)"}
    payload = {"files": {n: files[n].decode("utf-8") for n in FILES}, "plan": {"journeys": journeys}, "mutant": mutant}
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "TEMP", "TMP",
                                                        "SYSTEMROOT", "WINDIR", "PLAYWRIGHT_BROWSERS_PATH",
                                                        "AYVEN_CHROME_PATH"}}
    python = os.environ.get("AYVEN_REVIEW_PYTHON") or sys.executable
    try:
        out = subprocess.run([python, str(RUNNER)], input=json.dumps(payload), text=True, capture_output=True,
                             timeout=180, env=env, check=False)
        evidence = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"passed": False, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:1500]}
    return evidence if isinstance(evidence, dict) else {"passed": False, "status": "unavailable"}


def review(order_id: str, browser=run_journeys) -> dict:
    """Review exactly the bytes Workbench returned. Idempotent: a finished review is returned as it is."""
    from .workbench_bridge import ensure_tables, event
    from . import teams
    c = connect()
    try:
        ensure_tables(c)
        r = c.execute("SELECT * FROM workbench_orders WHERE id=? AND kind=?", (order_id, KIND)).fetchone()
        if not r:
            raise HTTPException(404, "Unknown prototype order")
        if r["state"] != "reviewing":
            return json.loads(r["review_json"] or "null") or {"state": r["state"]}
        files = {row["name"]: bytes(row["content"]) for row in
                 c.execute("SELECT name,content FROM workbench_artifacts WHERE order_id=?", (order_id,))}
        plan = json.loads(r["plan_json"])
        job_id = plan["job_id"]
    finally:
        c.close()
    assignment = teams.assign(job_id, "review", REVIEW_TEAM, inputs=teams.inputs_from_plan(plan), project=order_id)
    if assignment["status"] != "active":
        _store_review_wait(order_id, assignment)
        return {"state": "reviewing", "waiting_for": "priority_decision", "assignment": assignment}
    record = signed_prototype(files) or {}
    approved = plan["prototype"]["journeys"]
    held_out = [dict(j, requirement="[review] " + j["requirement"]) for j in (plan.get("review") or {}).get("checks") or []]
    live = browser(files, approved + held_out)
    checks = live.get("checks") or []
    mutant = browser(files, approved, mutant=True) if live.get("status") == "ok" else {}
    passed = [f"{c['requirement']} ({c['width']}px)" for c in checks if c.get("passed")]
    failed = [f"{c['requirement']} ({c['width']}px): {c.get('error') or 'failed'}"[:600] for c in checks
              if not c.get("passed")]
    expected = {(j["requirement"], w) for j in approved + held_out for w in WIDTHS}
    ran = {(c.get("requirement"), c.get("width")) for c in checks}
    if live.get("status") in ("disabled", "unavailable"):
        failed.append("Campus could not run its browser review: " + str(live.get("error") or live.get("status")))
    elif ran != expected:
        failed.append(f"Review ran {len(ran)} of {len(expected)} checks (stops at the first failing width)")
    for error in live.get("errors") or []:
        failed.append("Page error: " + str(error)[:300])
    if live.get("status") == "ok" and not (mutant.get("status") == "failed"):
        failed.append("With JavaScript disabled the approved journeys still pass: they do not test the app")
    mode = mode_of(record)
    outcome = {"stage": "review", "team": REVIEW_TEAM, "agent_id": assignment.get("agent_id"),
               "passed": passed, "failed": failed, "held_out_checks": len(held_out),
               "integrity": ["Workbench signature verified", "File bytes match the tested files",
                             "Built from the plan Lee approved"],
               "execution_mode": mode,
               "label": {"REAL": "Built by a verified local model", "FIXTURE": "TEST FIXTURE source, not a model build",
                         "SIMULATED": "SIMULATED, not real work"}[mode],
               "result": "passed" if not failed else "failed", "reviewed_at": int(time.time()),
               "prototype_files": list(FILES)}
    state = "review_passed" if not failed else "review_failed"
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        current = c.execute("SELECT state FROM workbench_orders WHERE id=?", (order_id,)).fetchone()
        if current["state"] != "reviewing":           # a concurrent review finished first
            c.commit()
            return json.loads(c.execute("SELECT review_json FROM workbench_orders WHERE id=?",
                                        (order_id,)).fetchone()["review_json"] or "null")
        c.execute("UPDATE workbench_orders SET state=?,review_json=?,updated_at=? WHERE id=?",
                  (state, json.dumps(outcome), int(time.time()), order_id))
        event(c, order_id, "review", "campus", f"Campus review {outcome['result']}: {len(passed)} passed, "
              f"{len(failed)} failed" + ("" if not failed else " — waiting for Lee's decision"))
        c.commit()
    finally:
        c.close()
    freed = teams.release(job_id, "review", REVIEW_TEAM)
    if freed.get("next_project"):
        start_review(freed["next_project"])       # the next job Lee put in line for this team
    return outcome


def _store_review_wait(order_id, assignment):
    from .workbench_bridge import event
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE workbench_orders SET review_json=?,updated_at=? WHERE id=?",
                  (json.dumps({"state": "reviewing", "waiting_for": "priority_decision", "assignment": assignment}),
                   int(time.time()), order_id))
        event(c, order_id, "review_waiting", "campus",
              f"Review team {assignment['team']} is busy; Campus recommended a priority and is waiting for Lee")
        c.commit()
    finally:
        c.close()


def start_review(order_id: str):
    """After the result transaction commits. Disabled in tests that drive review() directly."""
    if os.environ.get("AYVEN_PROTOTYPE_AUTO_REVIEW", "1") != "1":
        return
    with _LOCK:
        t = _REVIEWS.get(order_id)
        if t and t.is_alive():
            return
        t = threading.Thread(target=_safe_review, args=(order_id,), daemon=True)
        _REVIEWS[order_id] = t
        t.start()


def _safe_review(order_id):
    try:
        review(order_id)
    except Exception as exc:     # the order stays 'reviewing'; POST .../review retries
        print("Prototype review error", order_id, type(exc).__name__, str(exc)[:200], flush=True)


# ---------------------------------------------------------------- owner views and decisions

def view(r) -> dict:
    result = json.loads(r["result_json"] or "null") or {}
    review_data = json.loads(r["review_json"] or "null")
    plan = json.loads(r["plan_json"])
    state = r["state"]
    findings = review_data if review_data and review_data.get("stage") == "review" else result.get("findings")
    out = {"order_id": r["id"], "kind": KIND, "state": state, "job_id": plan["job_id"], "plan_id": plan["plan_id"],
           "plan_revision": plan["revision"], "title": plan["title"], "request_key": r["plan_key"],
           "fix_of": r["fix_of"], "execution_mode": result.get("execution_mode"),
           "build": {"verdict": result.get("verdict"), "build_id": result.get("build_id"),
                     "checks": (result.get("build") or {}).get("checks")},
           "findings": findings, "review": review_data,
           "decision_required": state in DECIDABLE,
           "decision": json.loads(r["decision_json"] or "null")}
    if state in ("review_passed", "review_failed"):
        out["prototype_path"] = f"/ayven/prototypes/{r['id']}/prototype/index.html"
    return out


def _row(c, order_id):
    from .workbench_bridge import ensure_tables
    ensure_tables(c)
    r = c.execute("SELECT * FROM workbench_orders WHERE id=? AND kind=?", (order_id, KIND)).fetchone()
    if not r:
        raise HTTPException(404, "Unknown prototype order")
    return r


@router.get("/ayven/prototypes")
def list_prototypes(limit: int = 50):
    """Owner: every prototype order, newest first, in the same shape as the single view (what Milo shows)."""
    from .workbench_bridge import ensure_tables
    c = connect()
    try:
        ensure_tables(c)
        rows = c.execute("SELECT * FROM workbench_orders WHERE kind=? ORDER BY created_at DESC, id LIMIT ?",
                         (KIND, max(1, min(200, int(limit))))).fetchall()
        return {"prototypes": [view(r) for r in rows]}
    finally:
        c.close()


@router.get("/ayven/prototypes/{order_id}")
def status(order_id: str):
    c = connect()
    try:
        return view(_row(c, order_id))
    finally:
        c.close()


@router.get("/ayven/prototypes/{order_id}/prototype/{name}")
def prototype_file(order_id: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "Not a prototype file")
    c = connect()
    try:
        r = _row(c, order_id)
        if r["state"] not in ("review_passed", "review_failed", "fix_requested", "closed"):
            raise HTTPException(409, "The prototype is available once Campus has reviewed it")
        a = c.execute("SELECT content FROM workbench_artifacts WHERE order_id=? AND name=?", (order_id, name)).fetchone()
    finally:
        c.close()
    if not a:
        raise HTTPException(404, "File not returned by Workbench")
    return Response(bytes(a["content"]), media_type=TYPES[name],
                    headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


@router.post("/ayven/prototypes/{order_id}/review")
def rerun_review(order_id: str, request: Request):
    from .ayven_os import is_read_only
    if is_read_only(request):
        raise HTTPException(403, "Read-only credential cannot start a review")
    return review(order_id)


@router.post("/ayven/prototypes/{order_id}/decision")
def decide(order_id: str, body: DecisionIn, request: Request):
    """Lee's decision after a failed review or build. Exactly one decision per order."""
    from .ayven_os import actor_of, is_read_only
    from .workbench_bridge import event
    if is_read_only(request):
        raise HTTPException(403, "Read-only credential cannot decide")
    if body.decision not in ("fix", "no_fix"):
        raise HTTPException(400, "decision must be 'fix' or 'no_fix'")
    who = actor_of(request)
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        r = _row(c, order_id)
        prior = json.loads(r["decision_json"] or "null")
        if prior:
            c.commit()
            if prior["decision"] != body.decision:
                raise HTTPException(409, "Lee already decided: " + prior["decision"])
            return {"order_id": order_id, "duplicate": True, **prior}
        if r["state"] not in DECIDABLE:
            c.commit()
            raise HTTPException(409, f"No decision is pending (state {r['state']})")
        decision = {"decision": body.decision, "note": body.note, "by": who, "at": int(time.time())}
        if body.decision == "fix":
            findings = (json.loads(r["review_json"] or "null") or {}).get("failed") or \
                ((json.loads(r["result_json"] or "{}")).get("findings") or {}).get("failed") or []
            n = 1 + c.execute("SELECT COUNT(*) FROM workbench_orders WHERE fix_of IS NOT NULL AND plan_key LIKE ?",
                              (r["plan_key"].split(":fix-")[0] + ":fix-%",)).fetchone()[0]
            key = r["plan_key"].split(":fix-")[0] + f":fix-{n}"
            fix_id = "proto-" + hashlib.sha256(key.encode()).hexdigest()[:16]
            _insert(c, fix_id, key, json.loads(r["plan_json"]), r["plan_sha256"], json.loads(r["approval_json"]),
                    r["max_minutes"] or 30, fix_of=order_id, findings=findings)
            decision["fix_order_id"] = fix_id
            new_state = "fix_requested"
        else:
            new_state = "closed"
        c.execute("UPDATE workbench_orders SET state=?,decision_json=?,updated_at=? WHERE id=?",
                  (new_state, json.dumps(decision), int(time.time()), order_id))
        event(c, order_id, "decision", "owner", f"Lee decided {body.decision}" +
              (f"; fix build {decision['fix_order_id']} queued" if body.decision == "fix" else "; no fix"))
        c.commit()
        return {"order_id": order_id, "duplicate": False, "state": new_state, **decision}
    finally:
        c.close()
