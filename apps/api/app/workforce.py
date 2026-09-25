from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import events
from .db import connect
from .distribution import route, update_package
from .models import complete_role, escalation_configured
from .tools import fetch_page, web_search

EMPLOYEES = ["research-e1", "research-e2", "research-e3"]
SUPERVISOR = "research-sup"
MANAGER = "research-mgr"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_agent(agent_id: str, **fields) -> None:
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()


def _insert_package(**kw) -> str:
    pid = kw.get("id") or str(uuid.uuid4())
    ts = now()
    conn = connect()
    conn.execute(
        """INSERT INTO work_packages(
            id,project_id,task_id,title,objective,origin,agent_id,department_id,
            stage,status,parent_id,tier,confidence,review_status,attempt_count,
            return_reason,model_role,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pid, kw["project_id"], kw.get("task_id"), kw["title"], kw["objective"], kw.get("origin", "milo"), kw.get("agent_id"), kw.get("department_id", "research"), kw.get("stage", "command"), kw.get("status", "assigned"), kw.get("parent_id"), kw.get("tier", "EMPLOYEE"), kw.get("confidence"), kw.get("review_status"), kw.get("attempt_count", 0), kw.get("return_reason"), kw.get("model_role", "EMPLOYEE"), ts, ts),
    )
    conn.commit()
    conn.close()
    return pid


def _pkg(pid: str):
    conn = connect()
    row = conn.execute("SELECT * FROM work_packages WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _confidence(sources: int, missing: bool, accepted: bool) -> float:
    score = 0.25 + min(0.4, sources * 0.08)
    if not missing:
        score += 0.2
    if accepted:
        score += 0.15
    return round(min(0.95, score), 2)


def _decompose(objective: str):
    text, _, _ = complete_role("MANAGER", "Split the objective into 3 short employee research tasks, one per line.", objective, max_tokens=220)
    lines = [ln.strip(" -*\t") for ln in text.splitlines() if 12 < len(ln.strip()) < 180]
    if len(lines) < 3:
        lines = [f"Identify candidates for: {objective[:80]}", f"Extract public commercial terms for: {objective[:80]}", f"List enquiry gaps for: {objective[:80]}"]
    return lines[:4]


def _employee_work(package_id: str, agent_id: str) -> None:
    pkg = _pkg(package_id)
    if not pkg:
        return
    update_package(package_id, stage="employee", status="researching", agent_id=agent_id)
    _set_agent(agent_id, status="researching", current_tool="web_search", last_summary="Employee searching", progress=0.3)
    events.emit("agent.using_tool", project_id=pkg["project_id"], agent_id=agent_id, department_id="research", status="researching", tool="web_search", summary=f"{agent_id} searching")
    hits = web_search(pkg["objective"], limit=4)
    notes = []
    nsrc = 0
    for hit in hits[:3]:
        url = hit.get("url") or ""
        if url.startswith("http"):
            page = fetch_page(url)
            snippet = (page.get("text") or hit.get("snippet") or "")[:400]
            conn = connect()
            conn.execute("INSERT INTO sources(id,package_id,url,title,snippet,note,created_at) VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), package_id, page.get("url") or url, page.get("title") or hit.get("title"), snippet[:400], "employee", now()))
            conn.commit()
            conn.close()
            nsrc += 1
            notes.append(f"{hit.get('title')}\n{url}\n{snippet[:240]}")
        elif hit.get("title"):
            notes.append(f"{hit.get('title')}\n{hit.get('snippet') or ''}")
    evidence = "\n---\n".join(notes)[:3500] or "No usable pages captured."
    findings, tokens, _ = complete_role("EMPLOYEE", "Extract facts only. Mark unknowns. Do not invent prices.", f"Task:\n{pkg['objective']}\n\nEvidence:\n{evidence}", max_tokens=450)
    missing = any(k in findings.lower() for k in ("unknown", "not public", "gap", "enquiry", "cannot verify"))
    update_package(package_id, findings=findings, status="review", stage="supervisor", confidence=_confidence(nsrc, missing, False), attempt_count=int(pkg.get("attempt_count") or 0) + 1, model_role="EMPLOYEE", next_action="Supervisor review")
    _set_agent(agent_id, status="idle", current_tool=None, last_summary="Submitted to supervisor", progress=1, tokens=tokens)
    events.emit("package.review", project_id=pkg["project_id"], agent_id=SUPERVISOR, department_id="research", status="review", summary="Employee work queued for supervisor")


def _supervisor_review(package_id: str) -> str:
    pkg = _pkg(package_id)
    findings = pkg.get("findings") or ""
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS c FROM sources WHERE package_id=?", (package_id,)).fetchone()["c"]
    conn.close()
    weak = n < 1 or len(findings) < 80
    decision = "RETURN" if weak and int(pkg.get("attempt_count") or 0) < 2 else "ACCEPT"
    text, _, _ = complete_role("SUPERVISOR", "Reply ACCEPT or RETURN and one sentence reason.", f"Sources={n}\nFindings:\n{findings[:1200]}", max_tokens=120)
    if "return" in text.lower() and int(pkg.get("attempt_count") or 0) < 2:
        decision = "RETURN"
    if "accept" in text.lower() and not weak:
        decision = "ACCEPT"
    _set_agent(SUPERVISOR, status="working", last_summary=f"Review {decision}", progress=0.6)
    if decision == "RETURN":
        update_package(package_id, stage="employee", status="returned", review_status="RETURN", return_reason="Supervisor: evidence insufficient")
        events.emit("package.returned", project_id=pkg["project_id"], agent_id=SUPERVISOR, department_id="research", status="returned", summary="Supervisor returned package to employee")
        _employee_work(package_id, pkg.get("agent_id") or EMPLOYEES[0])
        return "RETURN"
    update_package(package_id, review_status="ACCEPT", stage="manager", status="review", confidence=_confidence(n, False, True))
    events.emit("package.accepted", project_id=pkg["project_id"], agent_id=SUPERVISOR, department_id="research", status="accepted", summary="Supervisor accepted employee work")
    _set_agent(SUPERVISOR, status="idle", last_summary="Accepted", progress=1)
    return "ACCEPT"


def _manager_review(parent_id: str, child_ids: list[str]) -> None:
    conn = connect()
    kids = [dict(r) for r in conn.execute(f"SELECT * FROM work_packages WHERE id IN ({','.join('?'*len(child_ids))})", child_ids).fetchall()]
    conn.close()
    blob = "\n\n".join(f"{k['title']}: {(k.get('findings') or '')[:400]}" for k in kids)
    text, _, _ = complete_role("MANAGER", "Briefing: Findings, Gaps, Next action. Say APPROVAL if enquiry needed. Say ESCALATION if local work is exhausted.", blob or "No child findings.", max_tokens=700)
    need_appr = any(k in text.lower() for k in ("approval", "enquiry", "contact"))
    need_esc = "escalation" in text.lower() and not need_appr
    parent = _pkg(parent_id)
    _set_agent(MANAGER, status="working", last_summary="Manager consolidating", progress=0.8)
    if need_esc and not escalation_configured():
        update_package(parent_id, findings=text, stage="escalation_required", status="escalation_required", next_action="Lee must approve frontier escalation")
        events.emit("package.escalation_required", project_id=parent["project_id"], agent_id=MANAGER, department_id="research", status="escalation_required", summary="Frontier escalation requested — not called")
        _set_agent(MANAGER, status="idle", last_summary="Escalation required (not sent)")
        return
    update_package(parent_id, findings=text, stage="distribution", status="routing", requires_approval=1 if need_appr else 0, next_action="Lee approval" if need_appr else "Present to Milo")
    dest = route(parent_id)
    if dest == "approval":
        conn = connect()
        conn.execute("INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at) VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), parent.get("task_id") or parent_id, parent["project_id"], MANAGER, "Approve prepared enquiry before any send.", "pending", now()))
        conn.commit()
        conn.close()
        _set_agent(MANAGER, status="needs_approval", last_summary="Waiting for Lee")
        events.emit("approval.requested", project_id=parent["project_id"], agent_id=MANAGER, department_id="command", status="pending", summary="Approve prepared enquiry")
    else:
        _set_agent(MANAGER, status="idle", last_summary="Brief ready", progress=1)


def run_objective(project_id: str, objective: str, task_id: str | None = None) -> str:
    _set_agent(MANAGER, status="working", last_summary="Planning workforce", progress=0.2)
    parent = _insert_package(project_id=project_id, task_id=task_id, title="Research programme", objective=objective, origin="milo", agent_id=MANAGER, tier="MANAGER", model_role="MANAGER", stage="manager", status="planning")
    events.emit("package.created", project_id=project_id, agent_id=MANAGER, department_id="research", status="planning", summary="Manager opened parent work package")
    child_ids = []
    for i, title in enumerate(_decompose(objective)):
        emp = EMPLOYEES[i % len(EMPLOYEES)]
        cid = _insert_package(project_id=project_id, task_id=task_id, title=title[:80], objective=title, origin="manager", agent_id=emp, parent_id=parent, tier="EMPLOYEE", model_role="EMPLOYEE", stage="employee", status="assigned")
        child_ids.append(cid)
        events.emit("package.created", project_id=project_id, agent_id=emp, department_id="research", status="assigned", summary=f"Child package for {emp}")
        _employee_work(cid, emp)
        _supervisor_review(cid)
    _manager_review(parent, child_ids)
    return parent
