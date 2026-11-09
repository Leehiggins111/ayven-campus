"""Ayven's owner-approved prototype lifecycle."""
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import llm, prototype_review as review
from .ayven_os import actor_of, is_read_only
from .db import connect
from .workbench_bridge import ensure_tables

router = APIRouter(prefix="/ayven/prototype-plans")


class Brief(BaseModel):
    brief: str = Field(min_length=1, max_length=12000)
    request_key: str = Field(min_length=1, max_length=200)


class Decision(BaseModel):
    digest: str
    action: str
    notes: str = Field(default="", max_length=4000)


def table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS prototype_plans (
        id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, brief TEXT NOT NULL,
        plan TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL,
        order_id TEXT, approval TEXT
    )""")


def writable(request):
    if is_read_only(request):
        raise HTTPException(403, "Owner write access required")


def find(db, job_id):
    row = db.execute("SELECT * FROM prototype_plans WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Prototype plan not found")
    return row


def response(row):
    return {"job_id": row["id"], "plan": json.loads(row["plan"]), "digest": row["digest"],
            "state": row["state"], "order_id": row["order_id"]}


def make_plan(job_id, revision, brief, previous=None, notes=""):
    system = """Plan a small offline browser prototype. Return only a JSON object with title, scope,
features (array of strings), estimate (cost_gbp number and timeline string), and prototype
(kind offline-browser and journeys). Each journey has requirement, category interaction,
and steps with action, selector and optional value. Use fill/click/select/check for interaction
and text/value/visible/invalid for assertions. Include concrete expected results from the brief.
Use stable CSS selectors. Every journey must include interaction and assertion. Do not build anything."""
    raw, _ = llm.complete(system, json.dumps({"brief": brief, "previous": previous, "changes": notes}), 2400)
    try:
        plan = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        plan.update(schema="ayven.plan/v1", plan_id=job_id, job_id=job_id, revision=revision, brief=brief)
        assert isinstance(plan["title"], str) and plan["title"].strip()
        assert isinstance(plan["scope"], str) and plan["scope"].strip()
        assert isinstance(plan["features"], list) and plan["features"]
        assert all(isinstance(feature, str) and feature.strip() for feature in plan["features"])
        assert isinstance(plan["estimate"]["timeline"], str) and plan["estimate"]["timeline"].strip()
        assert type(plan["estimate"]["cost_gbp"]) in (int, float) and plan["estimate"]["cost_gbp"] >= 0
        assert plan["prototype"]["kind"] == "offline-browser"
        journeys = plan["prototype"]["journeys"]
        assert isinstance(journeys, list) and journeys
        for journey in journeys:
            actions = {step["action"] for step in journey["steps"]}
            assert actions & {"fill", "click", "select", "check"}
            assert actions & {"text", "value", "visible", "invalid"}
        return plan
    except (ValueError, TypeError, KeyError, AttributeError, AssertionError):
        raise HTTPException(502, "Planner did not produce a usable prototype plan") from None


@router.post("")
def create(body: Brief, request: Request):
    writable(request)
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(422, "A brief is required")
    with closing(connect()) as db:
        table(db)
        existing = db.execute("SELECT * FROM prototype_plans WHERE request_key=?", (body.request_key,)).fetchone()
        if not existing:
            job_id = "ayven-plan-" + uuid4().hex
            plan = make_plan(job_id, 1, brief)
            db.execute("""INSERT INTO prototype_plans (id,request_key,brief,plan,digest,state)
                       VALUES(?,?,?,?,?,'awaiting_approval') ON CONFLICT(request_key) DO NOTHING""",
                       (job_id, body.request_key, brief, json.dumps(plan), review.plan_sha256(plan)))
            db.commit()
            existing = db.execute("SELECT * FROM prototype_plans WHERE request_key=?", (body.request_key,)).fetchone()
        if existing["brief"] != brief:
            raise HTTPException(409, "Request key already belongs to a different brief")
        return response(existing)


@router.get("/{job_id}")
def read(job_id: str):
    with closing(connect()) as db:
        table(db)
        return response(find(db, job_id))


@router.post("/{job_id}/decision")
def decide(job_id: str, body: Decision, request: Request):
    writable(request)
    if body.action not in ("approve", "revise", "decline"):
        raise HTTPException(422, "Unknown decision")
    with closing(connect()) as db:
        table(db)
        previous = find(db, job_id)
        revised = None
        if body.action == "revise":
            if previous["digest"] != body.digest or previous["state"] != "awaiting_approval":
                raise HTTPException(409, "This plan is no longer awaiting this decision")
            if not body.notes.strip():
                raise HTTPException(422, "Describe the requested changes")
            plan = json.loads(previous["plan"])
            revised = make_plan(job_id, plan["revision"] + 1, previous["brief"], plan, body.notes)
        db.execute("BEGIN IMMEDIATE")
        row = find(db, job_id)
        if row["digest"] != body.digest:
            raise HTTPException(409, "This plan revision is stale")
        if body.action == "approve" and row["state"] == "queued":
            return response(row)
        if row["state"] != "awaiting_approval":
            raise HTTPException(409, "A decision has already been recorded")
        if revised is not None:
            db.execute("UPDATE prototype_plans SET plan=?, digest=? WHERE id=?",
                       (json.dumps(revised), review.plan_sha256(revised), job_id))
        elif body.action == "decline":
            db.execute("UPDATE prototype_plans SET state='declined' WHERE id=?", (job_id,))
        else:
            plan = json.loads(row["plan"])
            approval = review.ApprovalIn(
                approval_id=uuid4().hex, plan_id=plan["plan_id"], revision=plan["revision"],
                plan_sha256=row["digest"], decision="approved", approved_by=actor_of(request),
                approved_at=datetime.now(timezone.utc).isoformat(),
            )
            digest = review._validate(plan, approval)
            key = review.plan_key(plan)
            order_id = "proto-" + hashlib.sha256(key.encode()).hexdigest()[:16]
            ensure_tables(db)
            review._insert(db, order_id, key, plan, digest, approval.model_dump(), 30)
            db.execute("UPDATE prototype_plans SET state='queued', order_id=?, approval=? WHERE id=?",
                       (order_id, json.dumps(approval.model_dump()), job_id))
        db.commit()
        return response(find(db, job_id))
