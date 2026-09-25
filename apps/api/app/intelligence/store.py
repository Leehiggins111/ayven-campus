"""Persistence helpers for intelligence rows."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..db import connect
from ..distribution import update_package
from .think import strip_think


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_package(**kw) -> str:
    pid = kw.get("id") or str(uuid.uuid4())
    ts = now()
    conn = connect()
    conn.execute(
        """INSERT INTO work_packages(
            id,project_id,task_id,title,objective,origin,agent_id,department_id,
            stage,status,parent_id,tier,confidence,review_status,attempt_count,
            return_reason,model_role,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            pid, kw["project_id"], kw.get("task_id"), kw["title"], kw["objective"], kw.get("origin", "milo"),
            kw.get("agent_id"), kw.get("department_id", "research"), kw.get("stage", "command"),
            kw.get("status", "assigned"), kw.get("parent_id"), kw.get("tier", "EMPLOYEE"), kw.get("confidence"),
            kw.get("review_status"), kw.get("attempt_count", 0), kw.get("return_reason"),
            kw.get("model_role", "EMPLOYEE"), ts, ts,
        ),
    )
    conn.commit()
    conn.close()
    return pid


def package(pid: str) -> dict | None:
    conn = connect()
    row = conn.execute("SELECT * FROM work_packages WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def save_plan(package_id: str, plan: dict) -> str:
    plan_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO plans(id,package_id,objective,deliverable,plan_json,task_class,created_at) VALUES(?,?,?,?,?,?,?)",
        (plan_id, package_id, plan.get("objective"), plan.get("deliverable"), json.dumps(plan), plan.get("task_class"), now()),
    )
    conn.commit()
    conn.close()
    update_package(package_id, plan_id=plan_id, task_class=plan.get("task_class"))
    return plan_id


def save_skill(package_id: str, name: str, version: str, reason: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO skills_used(id,package_id,skill_name,version,reason,created_at) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), package_id, name, version, reason, now()),
    )
    conn.commit()
    conn.close()


def save_verification(package_id: str, stage: str, agent_id: str, decision: str, payload: dict) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO verification_results(id,package_id,stage,agent_id,decision,payload_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), package_id, stage, agent_id, decision, json.dumps(payload), now()),
    )
    conn.commit()
    conn.close()


def save_quality(package_id: str, payload: dict) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO quality_results(id,package_id,score_json,overall,created_at) VALUES(?,?,?,?,?)",
        (str(uuid.uuid4()), package_id, json.dumps(payload), payload.get("overall"), now()),
    )
    conn.commit()
    conn.close()


def save_model_call(package_id: str, role: str, task_class: str, meta: dict, text: str) -> None:
    usage_prompt = int(meta.get("prompt_tokens") or 0)
    usage_completion = int(meta.get("completion_tokens") or meta.get("tokens") or 0)
    conn = connect()
    conn.execute(
        """INSERT INTO model_calls(
            id,package_id,role,model_id,provider,task_class,prompt_tokens,completion_tokens,
            latency_s,est_cost_usd,backend,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), package_id, role, meta.get("model") or meta.get("model_id") or "",
            meta.get("provider") or meta.get("backend") or "", task_class, usage_prompt, usage_completion,
            float(meta.get("elapsed_s") or meta.get("generation_s") or 0), float(meta.get("est_cost_usd") or 0),
            meta.get("backend") or meta.get("execution") or "", now(),
        ),
    )
    conn.commit()
    conn.close()
    del text


def save_source(package_id: str, url: str, title: str, snippet: str, note: str) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO sources(id,package_id,url,title,snippet,note,created_at) VALUES(?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), package_id, url, title, strip_think(snippet)[:500], note, now()),
    )
    conn.commit()
    conn.close()


def save_observability(package_id: str, payload: dict) -> None:
    update_package(package_id, observability_json=json.dumps(payload))
