"""Campus team assignments and priority recommendations (first release, 9 Nov 2026).

Campus manages Ayven's teams across projects and assigns planning and review. Teams are the existing Campus
departments and their registered agents (db.SEED_DEPTS / agents) — no second registry. A team works on one job
per role at a time. When a second job needs a team that is busy, Campus does NOT pick a winner: it records a
priority recommendation with its reasons and waits for Lee's decision (relayed by Milo). Nothing a model says
changes an assignment; the recommendation is a deterministic, explained rule.

  GET  /ayven/teams                              teams, active and waiting assignments, pending decisions
  POST /ayven/teams/assign                       owner: {job_id, role, team, inputs?} -> active | waiting_priority
  POST /ayven/teams/{team}/release               owner: {job_id, role} the job no longer needs the team
  GET  /ayven/priority                           pending and decided priority decisions
  POST /ayven/priority/{decision_id}/decide      owner: {job_id} Lee chooses which job the team works on first
"""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import connect

router = APIRouter()
ROLES = ("planning", "review")
PLANNING_TEAM = "command"
# Which registered agent leads a role inside a team when the team has one (else the first agent in the team).
LEADS = {("command", "planning"): "milo", ("labs", "review"): "labs-validator",
         ("research", "planning"): "research-mgr", ("research", "review"): "verifier"}


def ensure_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS team_assignments (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL, role TEXT NOT NULL, team TEXT NOT NULL, agent_id TEXT,
        status TEXT NOT NULL, project TEXT, inputs_json TEXT NOT NULL DEFAULT '{}', decision_id TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(job_id, role, team))""")
    c.execute("""CREATE TABLE IF NOT EXISTS priority_decisions (
        id TEXT PRIMARY KEY, team TEXT NOT NULL, role TEXT NOT NULL, contenders_json TEXT NOT NULL,
        recommended_job TEXT NOT NULL, reasons_json TEXT NOT NULL, status TEXT NOT NULL, decided_job TEXT,
        decided_by TEXT, created_at INTEGER NOT NULL, decided_at INTEGER)""")


def inputs_from_plan(plan: dict) -> dict:
    """What the recommendation may use: Lee's stated deadline, the plan's estimate, and when it was approved."""
    est = plan.get("estimate") or {}
    return {"deadline": plan.get("deadline") or est.get("deadline"), "cost_gbp": est.get("cost_gbp"),
            "days": est.get("days"), "approved": True, "title": plan.get("title")}


def recommend(contenders: list[dict]) -> tuple[str, list[str]]:
    """Deterministic: (1) an approved plan before an unapproved one, (2) the earlier deadline, (3) the smaller
    estimated effort, (4) whoever asked first. Returns (job_id, reasons) — reasons are shown to Lee verbatim."""
    def key(a):
        i = a["inputs"]
        return (0 if i.get("approved") else 1, i.get("deadline") or "9999-12-31",
                float(i.get("days") if i.get("days") is not None else 1e9), a["created_at"])
    ranked = sorted(contenders, key=key)
    first, rest = ranked[0], ranked[1:]
    reasons = []
    for other in rest:
        a, b = first["inputs"], other["inputs"]
        name, other_name = a.get("title") or first["job_id"], b.get("title") or other["job_id"]
        if bool(a.get("approved")) != bool(b.get("approved")):
            reasons.append(f"{name} has an approved plan; {other_name} does not yet")
        elif (a.get("deadline") or "") != (b.get("deadline") or "") and a.get("deadline"):
            reasons.append(f"{name} is due {a['deadline']}, before {other_name} "
                           f"({b.get('deadline') or 'no deadline'})")
        elif a.get("days") is not None and a.get("days") != b.get("days"):
            reasons.append(f"{name} is the smaller job ({a['days']} vs {b.get('days', 'unknown')} days estimated)")
        else:
            reasons.append(f"{name} asked for the team first; no deadline or estimate separates them")
    reasons.append("This is a recommendation. Campus waits for Lee's decision before the team switches.")
    return first["job_id"], reasons


def _row_view(r):
    return {k: r[k] for k in ("id", "job_id", "role", "team", "agent_id", "status", "project", "decision_id",
                              "created_at", "updated_at")} | {"inputs": json.loads(r["inputs_json"] or "{}")}


def _lead(c, team, role):
    lead = LEADS.get((team, role))
    if lead and c.execute("SELECT 1 FROM agents WHERE id=? AND department_id=?", (lead, team)).fetchone():
        return lead
    row = c.execute("SELECT id FROM agents WHERE department_id=? ORDER BY id LIMIT 1", (team,)).fetchone()
    return row["id"] if row else None


def assign(job_id: str, role: str, team: str, *, inputs: dict | None = None, project: str | None = None) -> dict:
    """Idempotent per (job, role, team). Free team -> active. Busy team -> waiting_priority + a recommendation."""
    if role not in ROLES:
        raise HTTPException(400, "role must be planning or review")
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        if not c.execute("SELECT 1 FROM departments WHERE id=?", (team,)).fetchone():
            c.rollback()
            raise HTTPException(404, "Unknown team")
        existing = c.execute("SELECT * FROM team_assignments WHERE job_id=? AND role=? AND team=?",
                             (job_id, role, team)).fetchone()
        if existing and existing["status"] in ("active", "waiting_priority", "queued"):
            c.commit()
            return _row_view(existing)
        now = int(time.time())
        aid = "ta-" + hashlib.sha256(f"{job_id}|{role}|{team}".encode()).hexdigest()[:16]
        busy = c.execute("SELECT * FROM team_assignments WHERE team=? AND role=? AND status='active' AND job_id!=?",
                         (team, role, job_id)).fetchall()
        status = "waiting_priority" if busy else "active"
        c.execute("""INSERT OR REPLACE INTO team_assignments
            (id,job_id,role,team,agent_id,status,project,inputs_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (aid, job_id, role, team, _lead(c, team, role), status, project, json.dumps(inputs or {}), now, now))
        if busy:
            _open_decision(c, team, role, now)
        c.commit()
        return _row_view(c.execute("SELECT * FROM team_assignments WHERE id=?", (aid,)).fetchone())
    finally:
        c.close()


def _open_decision(c, team, role, now):
    """One open decision per team+role, covering every job contending for it right now."""
    contenders = [dict(r) | {"inputs": json.loads(r["inputs_json"] or "{}")} for r in c.execute(
        "SELECT * FROM team_assignments WHERE team=? AND role=? AND status IN ('active','waiting_priority') "
        "ORDER BY created_at, id", (team, role))]
    job, reasons = recommend(contenders)
    open_ = c.execute("SELECT id FROM priority_decisions WHERE team=? AND role=? AND status='awaiting_lee'",
                      (team, role)).fetchone()
    payload = json.dumps([{"job_id": x["job_id"], "status": x["status"], "inputs": x["inputs"]} for x in contenders])
    if open_:
        did = open_["id"]
        c.execute("UPDATE priority_decisions SET contenders_json=?,recommended_job=?,reasons_json=? WHERE id=?",
                  (payload, job, json.dumps(reasons), did))
    else:
        did = "pd-" + hashlib.sha256(f"{team}|{role}|{now}|{payload}".encode()).hexdigest()[:16]
        c.execute("INSERT INTO priority_decisions (id,team,role,contenders_json,recommended_job,reasons_json,status,"
                  "created_at) VALUES(?,?,?,?,?,?,'awaiting_lee',?)",
                  (did, team, role, payload, job, json.dumps(reasons), now))
    c.execute("UPDATE team_assignments SET decision_id=? WHERE team=? AND role=? AND status IN "
              "('active','waiting_priority')", (did, team, role))
    _event(c, "priority_recommended", f"{team} {role}: Campus recommends {job}; waiting for Lee", did)
    return did


def _event(c, kind, summary, ref):
    import uuid
    from datetime import datetime, timezone
    c.execute("INSERT INTO events(event_id,timestamp,type,status,summary,metadata) VALUES(?,?,?,?,?,?)",
              (str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), kind, "recorded", summary[:500],
               json.dumps({"ref": ref})))


def release(job_id: str, role: str, team: str) -> dict:
    """The job is done with the team. If Lee has already decided an order, the next job in it becomes active;
    if a decision is still pending, the team stays waiting for Lee rather than Campus choosing."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        now = int(time.time())
        c.execute("UPDATE team_assignments SET status='released',updated_at=? WHERE job_id=? AND role=? AND team=?",
                  (now, job_id, role, team))
        pending = c.execute("SELECT 1 FROM priority_decisions WHERE team=? AND role=? AND status='awaiting_lee'",
                            (team, role)).fetchone()
        nxt = None
        if not pending:
            nxt = c.execute("SELECT * FROM team_assignments WHERE team=? AND role=? AND status='queued' "
                            "ORDER BY updated_at, created_at LIMIT 1", (team, role)).fetchone()
            if nxt:
                c.execute("UPDATE team_assignments SET status='active',updated_at=? WHERE id=?", (now, nxt["id"]))
        c.commit()
        return {"released": job_id, "next_active": nxt["job_id"] if nxt else None,
                "next_project": nxt["project"] if nxt and role == "review" else None, "decision_pending": bool(pending)}
    finally:
        c.close()


class AssignIn(BaseModel):
    job_id: str = Field(min_length=1, max_length=200)
    role: str
    team: str = Field(min_length=1, max_length=60)
    inputs: dict = Field(default_factory=dict)


class ReleaseIn(BaseModel):
    job_id: str
    role: str


class DecideIn(BaseModel):
    job_id: str
    note: str = Field(default="", max_length=1000)


def _owner_write(request: Request):
    from .ayven_os import is_read_only
    if is_read_only(request):
        raise HTTPException(403, "Read-only credential cannot change assignments")


@router.get("/ayven/teams")
def teams_view():
    c = connect()
    try:
        ensure_tables(c)
        teams = []
        for d in c.execute("SELECT id,name FROM departments ORDER BY id"):
            agents = [dict(a) for a in c.execute("SELECT id,name,role FROM agents WHERE department_id=? ORDER BY id",
                                                 (d["id"],))]
            work = [_row_view(r) for r in c.execute(
                "SELECT * FROM team_assignments WHERE team=? AND status!='released' ORDER BY created_at", (d["id"],))]
            teams.append({"team": d["id"], "name": d["name"], "agents": agents, "assignments": work})
        return {"teams": teams, "pending_decisions": priority_view(c, pending_only=True)}
    finally:
        c.close()


def priority_view(c, pending_only=False):
    ensure_tables(c)
    sql = "SELECT * FROM priority_decisions" + (" WHERE status='awaiting_lee'" if pending_only else "") + \
          " ORDER BY created_at"
    return [{"id": r["id"], "team": r["team"], "role": r["role"], "status": r["status"],
             "contenders": json.loads(r["contenders_json"]), "recommended_job": r["recommended_job"],
             "reasons": json.loads(r["reasons_json"]), "decided_job": r["decided_job"],
             "decided_by": r["decided_by"], "decided_at": r["decided_at"]} for r in c.execute(sql)]


@router.post("/ayven/teams/assign")
def assign_route(body: AssignIn, request: Request):
    _owner_write(request)
    return assign(body.job_id, body.role, body.team, inputs=body.inputs)


@router.post("/ayven/teams/{team}/release")
def release_route(team: str, body: ReleaseIn, request: Request):
    _owner_write(request)
    return release(body.job_id, body.role, team)


@router.get("/ayven/priority")
def priority_route():
    c = connect()
    try:
        return {"decisions": priority_view(c)}
    finally:
        c.close()


@router.post("/ayven/priority/{decision_id}/decide")
def decide(decision_id: str, body: DecideIn, request: Request):
    """Lee's choice. The chosen job becomes active on the team; the others are queued behind it in order."""
    from .ayven_os import actor_of
    _owner_write(request)
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        ensure_tables(c)
        d = c.execute("SELECT * FROM priority_decisions WHERE id=?", (decision_id,)).fetchone()
        if not d:
            c.rollback()
            raise HTTPException(404, "Unknown decision")
        if d["status"] == "decided":
            c.commit()
            if d["decided_job"] != body.job_id:
                raise HTTPException(409, "Lee already decided: " + d["decided_job"])
            return {"id": decision_id, "duplicate": True, "decided_job": d["decided_job"]}
        jobs = [x["job_id"] for x in json.loads(d["contenders_json"])]
        if body.job_id not in jobs:
            c.rollback()
            raise HTTPException(400, "That job is not one of the contenders")
        now = int(time.time())
        who = actor_of(request)
        c.execute("UPDATE priority_decisions SET status='decided',decided_job=?,decided_by=?,decided_at=? WHERE id=?",
                  (body.job_id, who, now, decision_id))
        for job in jobs:
            c.execute("UPDATE team_assignments SET status=?,updated_at=? WHERE team=? AND role=? AND job_id=? "
                      "AND status IN ('active','waiting_priority')",
                      ("active" if job == body.job_id else "queued", now + (0 if job == body.job_id else 1),
                       d["team"], d["role"], job))
        _event(c, "priority_decided", f"Lee chose {body.job_id} for {d['team']} {d['role']}"
               + (" (Campus recommended the same)" if body.job_id == d["recommended_job"] else
                  f" (Campus had recommended {d['recommended_job']})"), decision_id)
        if not c.execute("SELECT 1 FROM team_assignments WHERE team=? AND role=? AND status='active'",
                         (d["team"], d["role"])).fetchone():
            # Lee chose a job that had already finished with the team: the next one in Lee's order starts.
            c.execute("UPDATE team_assignments SET status='active' WHERE id=(SELECT id FROM team_assignments "
                      "WHERE team=? AND role=? AND status='queued' ORDER BY updated_at, created_at LIMIT 1)",
                      (d["team"], d["role"]))
        chosen = c.execute("SELECT project FROM team_assignments WHERE team=? AND role=? AND status='active'",
                           (d["team"], d["role"])).fetchone()
        c.commit()
        if d["role"] == "review" and chosen and (chosen["project"] or "").startswith("proto-"):
            from .prototype_review import start_review
            start_review(chosen["project"])       # the review that was waiting for this decision
        return {"id": decision_id, "duplicate": False, "decided_job": body.job_id,
                "recommended_job": d["recommended_job"], "decided_by": who}
    finally:
        c.close()
