"""Goal contract: budget, acceptance criteria, owned tasks, handovers, QA, resume.

The intelligence engine still does research. This module is the record of who
owns each piece of work, what done means, and what must be true before a job
can be called complete.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import connect
from .intelligence.budget import (
    bind_job,
    budget_view,
    gbp,
    job_budget_usd,
    job_spent_usd,
    month_allows_new_job,
    record_intervention,
    retries as retry_count,
    usd_from_gbp,
)

_MAX_CRITERIA = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS goal_contracts (
            project_id TEXT PRIMARY KEY,
            acceptance_criteria TEXT NOT NULL,
            deadline TEXT,
            job_cap_usd REAL NOT NULL,
            allow_paid_fallback INTEGER NOT NULL DEFAULT 0,
            allow_publish INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS owned_tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            title TEXT NOT NULL,
            acceptance_criteria TEXT NOT NULL,
            status TEXT NOT NULL,
            output_path TEXT,
            depends_on TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS handovers (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            from_role TEXT NOT NULL,
            to_role TEXT NOT NULL,
            acceptance_criteria TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS qa_reviews (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            findings TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS blockers (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            package_id TEXT,
            kind TEXT NOT NULL,
            reason TEXT NOT NULL,
            needed_from_lee TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS consequential_actions (
            action_key TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS workforce_checkpoints (
            project_id TEXT PRIMARY KEY,
            stage TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS criteria_results (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            criterion TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    _add(conn, "goal_contracts", "job_cap_gbp", "REAL")
    _add(conn, "approvals", "reason", "TEXT")
    _add(conn, "approvals", "consequences", "TEXT")
    _add(conn, "approvals", "irreversible_effects", "TEXT")
    conn.commit()
    if own:
        conn.close()


def _add(conn, table: str, column: str, decl: str) -> None:
    have = {row["name"] if isinstance(row, dict) else row[1] for row in _pragma(conn, table)}
    if column not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _pragma(conn, table: str):
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        else:
            try:
                out.append({"name": row["name"]})
            except Exception:
                out.append({"name": row[1]})
    return out


def check_budget(job_cap_gbp: float | None = None, budget_usd: float | None = None) -> float:
    """The job cap in USD for enforcement. A budget is kept as sent, or refused with the reason.

    A pound budget is never converted and clamped: one the ceiling cannot support
    raises ValueError naming the ceiling, and nothing is stored.
    """
    import math

    if job_cap_gbp is not None:
        if not math.isfinite(float(job_cap_gbp)) or job_cap_gbp <= 0:
            raise ValueError("Set a budget above £0.")
        requested = usd_from_gbp(job_cap_gbp)
        label = f"£{float(job_cap_gbp):.2f}"
    elif budget_usd is not None:
        if not math.isfinite(float(budget_usd)) or budget_usd <= 0:
            raise ValueError("Set a budget above $0.")
        requested = float(budget_usd)
        label = f"${float(budget_usd):.2f}"
    else:
        return job_budget_usd()
    ceiling = job_budget_usd()
    if requested > ceiling + 1e-9:
        raise ValueError(
            f"Unsupported budget: {label} is above this server's per-job ceiling of "
            f"${ceiling:.2f} (about £{gbp(ceiling):.2f}, AYVEN_JOB_BUDGET_USD). "
            "The budget was not changed. Send a budget within the ceiling or raise the ceiling."
        )
    return requested


def open_goal(
    project_id: str,
    criteria: list[str],
    *,
    deadline: str | None = None,
    job_cap_gbp: float | None = None,
    budget_usd: float | None = None,
    allow_paid_fallback: bool = False,
    allow_publish: bool = False,
) -> dict:
    ensure_schema()
    cap = check_budget(job_cap_gbp, budget_usd)
    cap_gbp = float(job_cap_gbp) if job_cap_gbp is not None else None
    allowed, why = month_allows_new_job(cap)
    if not allowed:
        raise ValueError(why)
    clean = [item.strip() for item in criteria if item and item.strip()][:_MAX_CRITERIA]
    now = _now()
    conn = connect()
    conn.execute(
        """INSERT INTO goal_contracts(
            project_id, acceptance_criteria, deadline, job_cap_usd, job_cap_gbp, allow_paid_fallback, allow_publish, status, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
            acceptance_criteria=excluded.acceptance_criteria,
            deadline=excluded.deadline,
            job_cap_usd=excluded.job_cap_usd,
            job_cap_gbp=excluded.job_cap_gbp,
            allow_paid_fallback=excluded.allow_paid_fallback,
            allow_publish=excluded.allow_publish,
            updated_at=excluded.updated_at""",
        (project_id, json.dumps(clean), deadline, cap, cap_gbp, int(allow_paid_fallback), int(allow_publish), "running", now, now),
    )
    conn.commit()
    conn.close()
    bind_job(project_id)
    return get_goal(project_id)


def get_goal(project_id: str) -> dict | None:
    ensure_schema()
    conn = connect()
    row = conn.execute("SELECT * FROM goal_contracts WHERE project_id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return None
    item = dict(row)
    item["acceptance_criteria"] = json.loads(item["acceptance_criteria"] or "[]")
    # The pound budget Lee sent is shown exactly. Only a USD-only cap is converted for display.
    item["job_cap_gbp"] = item["job_cap_gbp"] if item.get("job_cap_gbp") is not None else gbp(item["job_cap_usd"])
    item["job_cap_currency"] = "GBP"
    item["spent_usd"] = job_spent_usd(project_id)
    item["spent_gbp"] = gbp(item["spent_usd"])
    item["allow_paid_fallback"] = bool(item["allow_paid_fallback"])
    item["allow_publish"] = bool(item["allow_publish"])
    return item


def create_owned_tasks(project_id: str, tasks: list[dict]) -> list[dict]:
    """Director split. Each task has an owner and its own acceptance criteria."""
    ensure_schema()
    now = _now()
    conn = connect()
    created = []
    for spec in tasks:
        task_id = spec.get("id") or str(uuid.uuid4())
        criteria = spec.get("acceptance_criteria") or []
        conn.execute(
            """INSERT OR IGNORE INTO owned_tasks(
                id, project_id, owner, title, acceptance_criteria, status, output_path, depends_on, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, project_id, spec["owner"], spec["title"], json.dumps(criteria),
                spec.get("status") or "queued", spec.get("output_path") or "", spec.get("depends_on") or "", now, now,
            ),
        )
        created.append(task_id)
    conn.commit()
    rows = conn.execute("SELECT * FROM owned_tasks WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    conn.close()
    return [_task(row) for row in rows if row["id"] in created or True]


def _task(row) -> dict:
    item = dict(row)
    item["acceptance_criteria"] = json.loads(item.get("acceptance_criteria") or "[]")
    return item


def split_goal(project_id: str, objective: str, criteria: list[str], constraints: list[str] | None = None) -> list[dict]:
    """The director's split. Milo's constraints are hard rules for the director and for QA."""
    hard = [f"Hard constraint: {item}" for item in (constraints or []) if item and str(item).strip()]
    research_criteria = [*(criteria or ["Every claim cites a source"]), *hard]
    qa_criteria = ["A defect is numbered and sent back to its owner", *hard]
    specs = [
        {"id": f"{project_id}:research", "owner": "research", "title": "Research with evidence", "acceptance_criteria": research_criteria, "output_path": "outputs/research.md", "depends_on": ""},
        {"id": f"{project_id}:commercial", "owner": "commercial", "title": "Commercial assessment", "acceptance_criteria": ["Prices come from the commercial file", *hard], "output_path": "outputs/commercial.md", "depends_on": f"{project_id}:research"},
        {"id": f"{project_id}:creation", "owner": "creation", "title": "Create the draft", "acceptance_criteria": [*(criteria or ["The draft matches the evidence"]), *hard], "output_path": "outputs/draft.md", "depends_on": f"{project_id}:commercial"},
        {"id": f"{project_id}:qa", "owner": "qa", "title": "Independent QA", "acceptance_criteria": qa_criteria, "output_path": "outputs/qa.md", "depends_on": f"{project_id}:creation"},
        {"id": f"{project_id}:listing", "owner": "sales", "title": "Listing draft and package", "acceptance_criteria": ["Nothing is published or sent", *hard], "output_path": "outputs/listing.md", "depends_on": f"{project_id}:qa"},
    ]
    tasks = create_owned_tasks(project_id, specs)
    if not list_handovers(project_id):
        handover(project_id, specs[0]["id"], "director", "research", research_criteria)
    return tasks


def list_tasks(project_id: str) -> list[dict]:
    ensure_schema()
    conn = connect()
    rows = conn.execute("SELECT * FROM owned_tasks WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    conn.close()
    return [_task(row) for row in rows]


def set_task_status(task_id: str, status: str) -> None:
    ensure_schema()
    conn = connect()
    conn.execute("UPDATE owned_tasks SET status=?, updated_at=? WHERE id=?", (status, _now(), task_id))
    conn.commit()
    conn.close()


def handover(project_id: str, task_id: str, from_role: str, to_role: str, criteria: list[str]) -> dict:
    ensure_schema()
    row = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "task_id": task_id,
        "from_role": from_role,
        "to_role": to_role,
        "acceptance_criteria": criteria,
        "created_at": _now(),
    }
    conn = connect()
    conn.execute(
        "INSERT INTO handovers(id, project_id, task_id, from_role, to_role, acceptance_criteria, created_at) VALUES(?,?,?,?,?,?,?)",
        (row["id"], project_id, task_id, from_role, to_role, json.dumps(criteria), row["created_at"]),
    )
    conn.execute("UPDATE owned_tasks SET status='in_progress', updated_at=? WHERE id=?", (row["created_at"], task_id))
    conn.commit()
    conn.close()
    return row


def list_handovers(project_id: str) -> list[dict]:
    ensure_schema()
    conn = connect()
    rows = conn.execute("SELECT * FROM handovers WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["acceptance_criteria"] = json.loads(item["acceptance_criteria"] or "[]")
        out.append(item)
    return out


def record_qa(project_id: str, task_id: str, verdict: str, findings: list[dict], attempt: int) -> dict:
    ensure_schema()
    row = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "task_id": task_id,
        "verdict": verdict,
        "findings": findings,
        "attempt": attempt,
        "created_at": _now(),
    }
    conn = connect()
    conn.execute(
        "INSERT INTO qa_reviews(id, project_id, task_id, verdict, findings, attempt, created_at) VALUES(?,?,?,?,?,?,?)",
        (row["id"], project_id, task_id, verdict, json.dumps(findings), attempt, row["created_at"]),
    )
    status = "rework" if verdict == "REJECT" else "done"
    conn.execute("UPDATE owned_tasks SET status=?, updated_at=? WHERE id=?", (status, row["created_at"], task_id))
    conn.commit()
    conn.close()
    return row


def list_qa(project_id: str) -> list[dict]:
    ensure_schema()
    conn = connect()
    rows = conn.execute("SELECT * FROM qa_reviews WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["findings"] = json.loads(item["findings"] or "[]")
        out.append(item)
    return out


def add_blocker(project_id: str, kind: str, reason: str, needed_from_lee: str, package_id: str = "") -> dict:
    ensure_schema()
    row = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "package_id": package_id,
        "kind": kind,
        "reason": reason,
        "needed_from_lee": needed_from_lee,
        "status": "open",
        "created_at": _now(),
    }
    conn = connect()
    conn.execute(
        """INSERT INTO blockers(id, project_id, package_id, kind, reason, needed_from_lee, status, created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (row["id"], project_id, package_id, kind, reason, needed_from_lee, "open", row["created_at"]),
    )
    conn.execute("UPDATE goal_contracts SET status=?, updated_at=? WHERE project_id=?", ("paused", row["created_at"], project_id))
    conn.commit()
    conn.close()
    return row


def list_blockers(project_id: str = "", package_id: str = "") -> list[dict]:
    ensure_schema()
    conn = connect()
    if package_id:
        rows = conn.execute(
            "SELECT * FROM blockers WHERE package_id=? AND status='open' ORDER BY created_at DESC",
            (package_id,),
        ).fetchall()
    elif project_id:
        rows = conn.execute(
            "SELECT * FROM blockers WHERE project_id=? AND status='open' ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM blockers WHERE status='open' ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def once(project_id: str, action_key: str, action) -> dict:
    """Run a consequential action at most once. A restart returns the stored result."""
    ensure_schema()
    key = f"{project_id}:{action_key}"
    conn = connect()
    existing = conn.execute("SELECT result_json FROM consequential_actions WHERE action_key=?", (key,)).fetchone()
    if existing:
        conn.close()
        payload = json.loads(existing["result_json"])
        payload["replayed"] = True
        return payload
    conn.close()
    result = action()
    body = dict(result or {})
    body["replayed"] = False
    conn = connect()
    conn.execute(
        "INSERT INTO consequential_actions(action_key, project_id, result_json, created_at) VALUES(?,?,?,?)",
        (key, project_id, json.dumps(body), _now()),
    )
    conn.commit()
    conn.close()
    return body


def checkpoint(project_id: str, stage: str, state: dict) -> None:
    ensure_schema()
    conn = connect()
    conn.execute(
        """INSERT INTO workforce_checkpoints(project_id, stage, state_json, updated_at) VALUES(?,?,?,?)
           ON CONFLICT(project_id) DO UPDATE SET stage=excluded.stage, state_json=excluded.state_json, updated_at=excluded.updated_at""",
        (project_id, stage, json.dumps(state), _now()),
    )
    conn.commit()
    conn.close()


def load_checkpoint(project_id: str) -> dict | None:
    ensure_schema()
    conn = connect()
    row = conn.execute("SELECT * FROM workforce_checkpoints WHERE project_id=?", (project_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"stage": row["stage"], "state": json.loads(row["state_json"]), "updated_at": row["updated_at"]}


def resume_incomplete() -> list[str]:
    """Jobs whose checkpoint says they were in progress. Completed actions are not repeated."""
    ensure_schema()
    conn = connect()
    rows = conn.execute(
        """SELECT c.project_id FROM workforce_checkpoints c
           JOIN goal_contracts g ON g.project_id=c.project_id
           WHERE g.status IN ('running', 'paused')"""
    ).fetchall()
    conn.close()
    resumed = []
    for row in rows:
        project_id = row["project_id"]
        point = load_checkpoint(project_id)
        if not point:
            continue
        checkpoint(project_id, point["stage"], {**point["state"], "resumed": True})
        resumed.append(project_id)
    return resumed


def save_criteria(project_id: str, results: list[dict]) -> None:
    ensure_schema()
    conn = connect()
    conn.execute("DELETE FROM criteria_results WHERE project_id=?", (project_id,))
    now = _now()
    for item in results:
        conn.execute(
            "INSERT INTO criteria_results(id, project_id, criterion, status, created_at) VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), project_id, item["criterion"], item["status"], now),
        )
    conn.commit()
    conn.close()


_CRITERIA_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "each", "for", "from", "has", "have", "in", "include",
    "includes", "is", "it", "its", "least", "must", "no", "not", "of", "on", "or", "should", "that", "the",
    "their", "this", "to", "with", "within", "without",
}


def _tokens(text: str) -> list[str]:
    """Whole tokens. '£25' and '£250' are different tokens; trailing punctuation is dropped."""
    import re

    words = re.findall(r"[a-z0-9£$][a-z0-9£$.,'-]*", (text or "").lower())
    return [_normalise_figure(w.strip(".,'-")) for w in words if w.strip(".,'-")]


def _normalise_figure(token: str) -> str:
    """'£25.00' is '£25', '1,000' is '1000', '2.50' is '2.5'. The currency sign is kept."""
    import re

    match = re.fullmatch(r"([£$]?)(\d[\d,]*)(?:\.(\d+))?", token)
    if not match:
        return token
    sign, whole, fraction = match.groups()
    whole = whole.replace(",", "").lstrip("0") or "0"
    fraction = (fraction or "").rstrip("0")
    return sign + whole + ("." + fraction if fraction else "")


def _criterion_terms(criterion: str) -> list[str]:
    # Figures are kept whatever their length: '3', '£5' and '$2' are checkable terms.
    return [
        w for w in _tokens(criterion)
        if any(ch.isdigit() for ch in w) or (len(w) >= 3 and w not in _CRITERIA_STOP)
    ]


def evaluate_criteria(project_id: str, report: str) -> list[dict]:
    """Check each acceptance criterion against the delivered report and store the result.

    Deterministic whole-token coverage, not a model judgement: ``met`` when at least
    70% of the criterion's terms appear as whole tokens in the report and every figure in it appears exactly, ``not_met`` otherwise,
    ``unverified`` when the criterion has no checkable term.
    """
    goal = get_goal(project_id)
    criteria = (goal or {}).get("acceptance_criteria") or []
    if not criteria:
        return []
    text = (report or "").lower()
    present = set(_tokens(report))
    results = []
    for criterion in criteria:
        terms = _criterion_terms(criterion)
        if not terms:
            status = "unverified"
        else:
            found = sum(1 for term in terms if term in present)
            # A figure such as £25 or 3 must appear exactly; near-miss figures are not a match.
            figures = all(term in present for term in terms if any(ch.isdigit() for ch in term))
            status = "met" if text.strip() and figures and found / len(terms) >= 0.7 else "not_met"
        results.append({"criterion": criterion, "status": status})
    save_criteria(project_id, results)
    return results


def criteria_for(project_id: str) -> list[dict]:
    ensure_schema()
    conn = connect()
    rows = conn.execute(
        "SELECT criterion, status FROM criteria_results WHERE project_id=? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def completion_allowed(project_id: str, deliverable_names: list[str], checks_passed: bool) -> tuple[bool, str]:
    goal = get_goal(project_id)
    if goal is None:
        return False, "The job has no goal contract."
    if not deliverable_names:
        return False, "A job cannot be completed without its deliverables."
    if not checks_passed:
        return False, "A job cannot be completed until its checks pass."
    criteria = criteria_for(project_id)
    if goal["acceptance_criteria"] and not criteria:
        return False, "Acceptance criteria have not been checked."
    if any(item["status"] == "not_met" for item in criteria):
        return False, "An acceptance criterion is not met."
    reviews = list_qa(project_id)
    if reviews and reviews[-1]["verdict"] != "PASS":
        return False, "The latest QA verdict is not a pass."
    return True, ""


def mark_complete(project_id: str, deliverable_names: list[str], checks_passed: bool) -> dict:
    ok, why = completion_allowed(project_id, deliverable_names, checks_passed)
    if not ok:
        raise ValueError(why)
    ensure_schema()
    conn = connect()
    conn.execute("UPDATE goal_contracts SET status='completed', updated_at=? WHERE project_id=?", (_now(), project_id))
    conn.execute("UPDATE projects SET status='complete' WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "status": "completed"}


def stop_job(project_id: str) -> dict:
    ensure_schema()
    now = _now()
    conn = connect()
    conn.execute("UPDATE goal_contracts SET status='stopped', updated_at=? WHERE project_id=?", (now, project_id))
    conn.execute("UPDATE projects SET status='stopped' WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    record_intervention(project_id, "Lee stopped the job")
    return {"ok": True, "status": "stopped"}


def raise_cap(project_id: str, job_cap_gbp: float) -> dict:
    cap = check_budget(job_cap_gbp)
    goal = get_goal(project_id)
    if not goal:
        raise KeyError(project_id)
    allowed, why = month_allows_new_job(cap)
    if not allowed:
        raise ValueError(why)
    conn = connect()
    conn.execute("UPDATE goal_contracts SET job_cap_usd=?, job_cap_gbp=?, status='running', updated_at=? WHERE project_id=?", (cap, float(job_cap_gbp), _now(), project_id))
    conn.execute("UPDATE blockers SET status='closed' WHERE project_id=? AND kind='cap' AND status='open'", (project_id,))
    conn.commit()
    conn.close()
    record_intervention(project_id, "Lee raised the job budget")
    return get_goal(project_id)


def allow_fallback(project_id: str) -> dict:
    conn = connect()
    conn.execute(
        "UPDATE goal_contracts SET allow_paid_fallback=1, updated_at=? WHERE project_id=?",
        (_now(), project_id),
    )
    conn.execute("UPDATE blockers SET status='closed' WHERE project_id=? AND kind='fallback' AND status='open'", (project_id,))
    conn.commit()
    conn.close()
    record_intervention(project_id, "Lee allowed paid models for this job")
    return get_goal(project_id)


def run_metrics(project_id: str, started_at: str, finished_at: str | None = None) -> dict:
    import os

    from .intelligence.budget import ensure_schema as budget_schema
    from .intelligence.budget import interventions

    budget_schema()
    conn = connect()
    rows = conn.execute(
        "SELECT role, input_tokens, output_tokens, amount_usd FROM spend_ledger WHERE job_id=? AND committed=1 AND kind!='intervention'",
        (project_id,),
    ).fetchall()
    conn.close()
    tokens: dict[str, dict[str, int]] = {}
    cost = 0.0
    for row in rows:
        role = row["role"]
        bucket = tokens.setdefault(role, {"input": 0, "output": 0})
        bucket["input"] += int(row["input_tokens"] or 0)
        bucket["output"] += int(row["output_tokens"] or 0)
        cost += float(row["amount_usd"] or 0)
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    wall = max(0.0, (end - start).total_seconds())
    simulated = os.environ.get("AYVEN_LLM_STUB", "1") != "0"
    return {
        "simulated": simulated,
        "label": "SIMULATED" if simulated else "LIVE",
        "wall_time_s": round(wall, 3),
        "tokens_by_role": tokens,
        "compute_cost_usd": round(cost, 6),
        "compute_cost_gbp": gbp(cost),
        "retries": retry_count(project_id),
        "owner_interventions": interventions(project_id),
        "month": budget_view(),
    }


def halt_for_cap(project_id: str, package_id: str = "") -> dict:
    goal = get_goal(project_id) or {}
    cap = goal.get("job_cap_gbp")
    reason = f"Paused: this job has used its £{cap} budget." if cap is not None else "Paused: this job has used its budget."
    blocker = add_blocker(project_id, "cap", reason, "Raise this job's budget or stop the job.", package_id)
    _approval(project_id, package_id, reason, "The job stays paused.", "")
    return blocker


def _approval(project_id: str, package_id: str, summary: str, consequences: str, irreversible: str) -> None:
    conn = connect()
    conn.execute(
        """INSERT INTO approvals(id, task_id, project_id, agent_id, summary, status, created_at, consequences, irreversible_effects)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), package_id or project_id, project_id, "milo", summary, "pending", _now(), consequences, irreversible),
    )
    conn.commit()
    conn.close()
