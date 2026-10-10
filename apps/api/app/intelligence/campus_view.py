"""Campus reads this view. It is assembled from the ledger, not from a timer."""

from __future__ import annotations

import json
import re
from datetime import datetime

from .. import events
from ..db import connect
from ..distribution import update_package
from .deliverable import clip_at_boundary
from .think import strip_think

VISUAL_STATES = (
    "IDLE",
    "PLANNING",
    "RESEARCHING",
    "USING_TOOL",
    "WRITING",
    "REVIEWING",
    "REPAIRING",
    "WAITING",
    "NEEDS_APPROVAL",
    "COMPLETED",
    "FAILED",
)

_STATUS = {
    "IDLE": "idle",
    "PLANNING": "working",
    "RESEARCHING": "researching",
    "USING_TOOL": "using_tool",
    "WRITING": "working",
    "REVIEWING": "working",
    "REPAIRING": "working",
    "WAITING": "waiting",
    "NEEDS_APPROVAL": "needs_approval",
    "COMPLETED": "idle",
    "FAILED": "error",
}

PIPELINE = (
    "REQUEST",
    "PLANNING",
    "RESEARCH",
    "EVIDENCE",
    "EMPLOYEE",
    "DRAFT",
    "SUPERVISOR",
    "REPAIR",
    "MANAGER",
    "APPROVAL",
    "COMPLETE",
)


def set_visual(agent_id: str, visual: str, **fields) -> None:
    """Persist the agent state the campus draws. Status stays the legacy word."""
    visual = visual if visual in VISUAL_STATES else "IDLE"
    fields["visual_state"] = visual
    fields.setdefault("status", _STATUS[visual])
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()
    progress = fields.get("progress")
    events.emit(
        "agent.visual",
        agent_id=agent_id,
        status=visual,
        summary=str(fields.get("last_summary") or visual)[:240],
        progress=float(progress) if isinstance(progress, (int, float)) else 0.0,
        tool=fields.get("current_tool") or None,
    )


def mark_stage(package_id: str, stage: str, *, project_id: str = "", task_id: str = "") -> None:
    update_package(package_id, campus_stage=stage)
    events.emit(
        "package.stage",
        project_id=project_id or None,
        task_id=task_id or None,
        status=stage,
        summary=f"Stage {stage}",
    )


def public_text(value: str, limit: int = 280) -> str:
    text = strip_think(value or "")
    lowered = text.lower()
    if "<think" in lowered or "chain of thought" in lowered or "chain-of-thought" in lowered:
        return ""
    return " ".join(text.split())[:limit]


def owner_findings(value: str) -> str:
    """The stored plan, with reasoning tags removed and section text left whole."""
    return strip_think(value or "").strip()


def _summary_clip(text: str) -> str:
    flat = " ".join((text or "").split())
    return clip_at_boundary(flat, 400)


def readable_prose(value: str, limit: int = 1200) -> str:
    """Plain sentences for a business owner. Markup is not part of the result."""
    text = public_text(value, max(limit * 4, limit))
    text = re.sub(r"```.*?```", " ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((?:https?://|/)[^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[#>*_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].strip()
    return clipped or text[:limit]


def build_workflow(stage: str, *, had_repair: bool, needs_gate: bool, clarifying: bool, rejected: bool, early_clarification: bool = False, unresolved: bool = False) -> list[dict]:
    """Production order: research is collected before the employee drafts.

    A clarification that stops the package before research is drawn after planning,
    because those later stages have not run.
    """
    if early_clarification:
        steps = ["REQUEST", "PLANNING", "CLARIFICATION", "RESEARCH", "EVIDENCE", "EMPLOYEE", "DRAFT", "SUPERVISOR", "REPAIR", "MANAGER", "COMPLETE"]
        current = "CLARIFICATION"
    else:
        steps = [("CLARIFICATION" if clarifying and name == "APPROVAL" else name) for name in PIPELINE]
        current = "CLARIFICATION" if clarifying else (stage or "REQUEST")
    if current == "REJECTED":
        current = "COMPLETE"
    if unresolved:
        steps = ["UNRESOLVED" if name == "COMPLETE" else name for name in steps]
        current = "UNRESOLVED"
    if current not in steps:
        current = "REQUEST"
    index = steps.index(current)
    rows = []
    for i, name in enumerate(steps):
        if name == "REPAIR" and not had_repair and i != index:
            state = "skipped"
        elif name == "APPROVAL" and not needs_gate and current == "COMPLETE":
            state = "skipped"
        elif i < index:
            state = "done"
        elif i == index:
            state = "rejected" if rejected and name == "COMPLETE" else "current"
        else:
            state = "upcoming"
        rows.append({"id": name, "state": state})
    return rows


def _elapsed(created: str, updated: str) -> str:
    try:
        start = datetime.fromisoformat(created)
        end = datetime.fromisoformat(updated)
        seconds = max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError):
        return "unknown"
    if seconds < 1:
        return "under 1s"
    if seconds < 90:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"


def _loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def campus_view(package_id: str = "") -> dict:
    conn = connect()
    if package_id:
        row = conn.execute("SELECT * FROM work_packages WHERE id=?", (package_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM work_packages WHERE parent_id IS NULL ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        conn.close()
        return _empty()
    package = dict(row)
    children = [
        dict(item)
        for item in conn.execute("SELECT * FROM work_packages WHERE parent_id=?", (package["id"],)).fetchall()
    ]
    claim_ids = [package["id"], *[child["id"] for child in children]]
    placeholders = ",".join("?" for _ in claim_ids)
    claims = [
        dict(item)
        for item in conn.execute(
            f"SELECT * FROM claims WHERE package_id IN ({placeholders}) ORDER BY created_at DESC LIMIT 24",
            claim_ids,
        ).fetchall()
    ]
    agents = [dict(item) for item in conn.execute("SELECT * FROM agents").fetchall()]
    approvals = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM approvals WHERE project_id=? OR task_id=? ORDER BY created_at DESC",
            (package.get("project_id"), package.get("task_id") or package["id"]),
        ).fetchall()
    ]
    conn.close()
    obs = _loads(package.get("observability_json"))
    plan = obs.get("plan") or {}
    workflow_state = package.get("workflow_state") or ""
    stage = package.get("campus_stage") or _stage_from_workflow(workflow_state)
    clarifying = workflow_state == "AWAITING_CLARIFICATION" or stage == "CLARIFICATION"
    rejected = workflow_state == "FAILED" or stage == "REJECTED"
    unresolved = workflow_state == "UNRESOLVED" or stage == "UNRESOLVED"
    repairs = _repairs(obs.get("repairs") or [], claims)
    pending = next((item for item in approvals if item.get("status") == "pending"), None)
    needs_you = workflow_state == "AWAITING_APPROVAL" and pending is not None
    finished = (workflow_state == "COMPLETED" or stage == "COMPLETE") and not unresolved
    tools = list(plan.get("required_tools") or [])
    skills = [part for part in (package.get("selected_skills") or "").split(",") if part]
    if not skills:
        skills = list(plan.get("required_skills") or obs.get("skills") or [])
    research_rows = _research(obs)
    trust = _trust(claims, obs)
    who = _who(agents, package)
    doing = _doing(stage, package, clarifying, needs_you, finished, rejected, unresolved)
    why = public_text(
        (obs.get("resolution") or {}).get("reason")
        or (obs.get("resolution") or {}).get("rationale")
        or package.get("objective")
        or "",
        220,
    )
    question = public_text(package.get("clarification_question") or "", 400)
    approval = _approval_card(pending) if pending else None
    evaluation = obs.get("evaluation") or {}
    measured = obs.get("measured_cost_usd")
    cost = measured if isinstance(measured, (int, float)) else "unknown"
    result = None
    supported = sum(1 for claim in claims if claim.get("status") == "SUPPORTED")
    if finished or rejected or unresolved:
        result = _result(package, obs, evaluation, trust, cost, supported=supported, unresolved=unresolved)
    supervisor_rejected = sum(
        1
        for audit in (obs.get("supervisor_decisions") or [])
        if (audit.get("decision") or "") not in ("", "ACCEPT")
    )
    return {
        "package_id": package["id"],
        "project_id": package.get("project_id") or "",
        "objective": public_text(package.get("objective") or "", 400),
        "doing": doing,
        "who": who,
        "why": why or "The objective Lee submitted.",
        "stage": workflow_state or stage,
        "stage_label": _human_stage(workflow_state, stage, clarifying, rejected, unresolved),
        "workflow": build_workflow(
            "CLARIFICATION" if clarifying else ("REJECTED" if rejected else stage),
            had_repair=bool(repairs),
            needs_gate=needs_you or bool(package.get("requires_approval")) or workflow_state in ("AWAITING_APPROVAL", "APPROVED"),
            clarifying=clarifying,
            rejected=rejected,
            early_clarification=clarifying and not package.get("manager_decision"),
            unresolved=unresolved,
        ),
        "tools": tools,
        "skills": skills,
        "research": research_rows,
        "trust": trust,
        "supervisor_rejected": supervisor_rejected,
        "repairing": stage == "REPAIR" or any(not item["resolved"] for item in repairs),
        "repairs": repairs,
        "manager": package.get("manager_decision") or "",
        "needs_you": needs_you,
        "needs_clarification": clarifying and not finished,
        "question": question,
        "approval": approval,
        "finished": finished,
        "unresolved": unresolved,
        "rejected": rejected,
        "result": result,
        "elapsed": _elapsed(package.get("created_at") or "", package.get("updated_at") or ""),
        "cost": cost,
        "tokens": int(obs.get("tokens") or (evaluation.get("stats") or {}).get("tokens") or 0),
        "agents": [
            {
                "id": agent["id"],
                "name": agent.get("name") or agent["id"],
                "role": agent.get("role") or "",
                "visual_state": agent.get("visual_state") or "IDLE",
                "status": agent.get("status") or "idle",
                "summary": public_text(agent.get("last_summary") or "", 180),
            }
            for agent in agents
        ],
        "evidence": _evidence(claims, children, package, obs),
    }


def _empty() -> dict:
    return {
        "package_id": "",
        "doing": "Idle",
        "who": "Nobody is assigned",
        "why": "No work package is open.",
        "stage": "IDLE",
        "stage_label": "IDLE",
        "workflow": build_workflow("REQUEST", had_repair=False, needs_gate=False, clarifying=False, rejected=False),
        "tools": [],
        "skills": [],
        "research": [],
        "trust": "nothing in progress",
        "supervisor_rejected": 0,
        "repairing": False,
        "repairs": [],
        "manager": "",
        "needs_you": False,
        "needs_clarification": False,
        "question": "",
        "approval": None,
        "finished": False,
        "unresolved": False,
        "rejected": False,
        "result": None,
        "elapsed": "unknown",
        "cost": "unknown",
        "tokens": 0,
        "agents": [],
        "evidence": [],
        "objective": "",
        "project_id": "",
    }


def _stage_from_workflow(state: str) -> str:
    return {
        "DRAFT": "REQUEST",
        "IN_PROGRESS": "PLANNING",
        "UNDER_REVIEW": "SUPERVISOR",
        "REPAIRING": "REPAIR",
        "READY": "MANAGER",
        "AWAITING_APPROVAL": "APPROVAL",
        "AWAITING_CLARIFICATION": "CLARIFICATION",
        "APPROVED": "APPROVAL",
        "ACTIONING": "COMPLETE",
        "COMPLETED": "COMPLETE",
        "UNRESOLVED": "UNRESOLVED",
        "FAILED": "REJECTED",
        "ESCALATED": "APPROVAL",
    }.get(state or "", "REQUEST")


def _human_stage(workflow_state: str, stage: str, clarifying: bool, rejected: bool, unresolved: bool) -> str:
    if unresolved:
        return "Unresolved"
    if rejected:
        return "Rejected"
    if clarifying:
        return "Needs clarification"
    words = {
        "REQUEST": "Request received",
        "PLANNING": "Planning",
        "RESEARCH": "Researching",
        "EVIDENCE": "Checking evidence",
        "EMPLOYEE": "Drafting",
        "DRAFT": "Writing",
        "SUPERVISOR": "Supervisor review",
        "REPAIR": "Being repaired",
        "MANAGER": "Manager decision",
        "APPROVAL": "Needs your approval",
        "CLARIFICATION": "Needs clarification",
        "COMPLETE": "Complete",
        "REJECTED": "Rejected",
        "UNRESOLVED": "Unresolved",
        "AWAITING_APPROVAL": "Needs your approval",
        "AWAITING_CLARIFICATION": "Needs clarification",
        "COMPLETED": "Complete",
        "FAILED": "Rejected",
        "IN_PROGRESS": "In progress",
    }
    return words.get(stage) or words.get(workflow_state) or "In progress"


def _doing(stage: str, package: dict, clarifying: bool, needs_you: bool, finished: bool, rejected: bool, unresolved: bool = False) -> str:
    if unresolved:
        return "Unresolved. The evidence is not enough to call this finished."
    if rejected:
        return "Rejected. Nothing was sent."
    if finished:
        return "Complete"
    if clarifying:
        return "Needs clarification before it can continue"
    if needs_you:
        return "Waiting for Lee to approve the next step"
    return {
        "REQUEST": "Request received",
        "PLANNING": "Planning the work",
        "RESEARCH": "Researching",
        "EVIDENCE": "Checking evidence",
        "EMPLOYEE": "Employee drafting",
        "DRAFT": "Writing the draft",
        "SUPERVISOR": "Supervisor reviewing",
        "REPAIR": "Repairing supervisor findings",
        "MANAGER": "Manager deciding",
        "APPROVAL": "Waiting for approval",
        "COMPLETE": "Complete",
    }.get(stage or "", package.get("manager_decision") or "Working")


def _who(agents: list[dict], package: dict) -> str:
    active = [
        agent.get("name") or agent["id"]
        for agent in agents
        if (agent.get("visual_state") or "IDLE") not in ("IDLE", "")
    ]
    if active:
        return ", ".join(active[:4])
    owner = package.get("agent_id") or "milo"
    match = next((agent.get("name") for agent in agents if agent["id"] == owner), owner)
    return str(match)


def _research(obs: dict) -> list[str]:
    rows = []
    for page in obs.get("pages") or []:
        if page:
            rows.append(public_text(str(page), 160))
    for gap in (obs.get("failures") or [])[:3]:
        if isinstance(gap, dict) and gap.get("error"):
            rows.append("Gap: " + public_text(str(gap.get("error")), 160))
    return rows[:6]


def _trust(claims: list[dict], obs: dict) -> str:
    if not claims and not obs:
        return "nothing checked yet"
    supported = sum(1 for claim in claims if claim.get("status") == "SUPPORTED")
    weak = sum(1 for claim in claims if claim.get("status") in ("UNVERIFIED", "CONTRADICTED", "DISPROVED"))
    if obs.get("errors"):
        return f"Degraded. {supported} supported, {weak} weak or contradicted."
    return f"{supported} supported, {weak} weak or contradicted."


def _repairs(actions: list, claims: list[dict]) -> list[dict]:
    by_id = {claim.get("id"): claim for claim in claims}
    rows = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        claim = by_id.get(action.get("claim_id")) or {}
        claim_text = public_text(claim.get("claim_text") or action.get("evidence") or "", 180)
        resolution = public_text(action.get("detail") or "", 240)
        kind = str(action.get("action") or "RESEARCH_MORE")
        rows.append({
            "claim": claim_text or "A published claim",
            "problem": public_text(action.get("problem") or resolution or kind, 220),
            "repair_type": kind,
            "status": "repaired" if action.get("resolved") else "repairing",
            "resolved": bool(action.get("resolved")),
            "resolution": resolution or kind,
        })
    if rows:
        return rows[:8]
    for claim in claims:
        history = claim.get("repair_history") or ""
        if not history or history in ("[]", "null"):
            continue
        try:
            parsed = json.loads(history)
        except json.JSONDecodeError:
            parsed = []
        if not isinstance(parsed, list) or not parsed:
            continue
        last = parsed[-1] if isinstance(parsed[-1], dict) else {"action": "RESEARCH_MORE", "detail": str(parsed[-1])}
        rows.append({
            "claim": public_text(claim.get("claim_text") or "", 180) or "A published claim",
            "problem": public_text(last.get("detail") or last.get("action") or "", 220),
            "repair_type": str(last.get("action") or "RESEARCH_MORE"),
            "status": "repaired",
            "resolved": True,
            "resolution": public_text(last.get("detail") or "", 240),
        })
    return rows[:8]


def _approval_card(row: dict | None) -> dict | None:
    if not row:
        return None
    context = _loads(row.get("context_json"))
    evidence = context.get("evidence") if isinstance(context.get("evidence"), list) else []
    clean = []
    for item in evidence[:4]:
        if isinstance(item, dict):
            clean.append({
                "source": public_text(str(item.get("source") or ""), 160),
                "note": public_text(str(item.get("note") or ""), 160),
            })
    return {
        "id": row["id"],
        "what": public_text(context.get("what") or row.get("summary") or "", 300),
        "why": public_text(context.get("why") or "A person must approve the next external action.", 300),
        "if_approved": public_text(context.get("if_approved") or "The same work package continues to completion. Nothing is sent externally.", 240),
        "if_rejected": public_text(context.get("if_rejected") or "The work package ends rejected. Nothing is sent.", 240),
        "evidence": clean,
    }


def _provenance(claim: dict, mode: str) -> tuple[str, str]:
    freshness = (claim.get("freshness") or "").upper()
    source_type = (claim.get("source_type") or "").upper()
    url = (claim.get("source_url") or "").strip()
    title = readable_prose(claim.get("source_title") or "", 120)
    when = (claim.get("retrieved_at") or "").strip()
    if (claim.get("claim_type") or "") == "SOURCE_FAILURE" or (
        (claim.get("status") or "") == "UNVERIFIED" and freshness == "UNKNOWN"
    ):
        reason = readable_prose(claim.get("evidence_text") or claim.get("claim_text") or "The page could not be opened.", 180)
        return "UNRESOLVED", reason or "The page could not be opened."
    if source_type == "INPUT" or freshness == "INPUT":
        return "USER-PROVIDED", "This came from the request, not from a web page."
    if freshness == "LIVE" and url.startswith("http") and mode == "live":
        when_bit = f" Retrieved {when}." if when else ""
        return "LIVE", f"Opened {title or url}.{when_bit}"
    if freshness.startswith("FIXTURE") or (mode in {"fixtures", "fixture", "stub"} and (url or title)):
        name = title or url or "a local snapshot"
        return "FIXTURE", f"{name} is a local fixture. It was not opened on the live web."
    if url.startswith("http") and mode == "live":
        return "LIVE", f"Opened {title or url}."
    if url and mode != "live":
        return "UNRESOLVED", "A link was recorded, but the page was not opened in live research."
    return "UNRESOLVED", "No opened page is linked to this claim."


def _evidence(claims: list[dict], children: list[dict], package: dict, obs: dict | None = None) -> list[dict]:
    obs = obs or {}
    mode = str(obs.get("research_mode") or "").lower()
    supervisor = {child["id"]: child.get("supervisor_decision") or "" for child in children}
    parent_supervisor = package.get("supervisor_decision") or ""
    rows = []
    for claim in claims:
        text = readable_prose(claim.get("claim_text") or "", 240)
        if not text:
            continue
        label, note = _provenance(claim, mode)
        rows.append({
            "claim": text,
            "provenance": label,
            "sentence": f"{label}. {note}",
            "support": claim.get("status") or "UNVERIFIED",
            "source": readable_prose(claim.get("source_url") or claim.get("source_title") or "", 180),
            "source_type": claim.get("source_type") or "",
            "authority": claim.get("authority") or "",
            "locator": claim.get("locator") or "",
            "file_hash": claim.get("file_hash") or "",
            "supervisor": supervisor.get(claim.get("package_id")) or parent_supervisor or "",
        })
    for gap in obs.get("failures") or []:
        if not isinstance(gap, dict) or not gap.get("error"):
            continue
        reason = readable_prose(str(gap.get("error")), 180)
        rows.append({
            "claim": readable_prose(str(gap.get("query") or "A requested page"), 180),
            "provenance": "UNRESOLVED",
            "sentence": f"UNRESOLVED. The page could not be opened. {reason}",
            "support": "UNVERIFIED",
            "source": readable_prose(str(gap.get("url") or ""), 180),
            "source_type": "",
            "authority": "",
            "locator": "",
            "file_hash": "",
            "supervisor": "",
        })
    return rows[:12]


def _result(package: dict, obs: dict, evaluation: dict, trust: str, cost: str, *, supported: int = 0, unresolved: bool = False) -> dict:
    gaps = []
    for item in (obs.get("plan") or {}).get("unknowns") or []:
        text = readable_prose(str(item), 180)
        if text:
            gaps.append(text)
    findings = owner_findings(package.get("findings") or "")
    deliverable = owner_findings((obs.get("plan") or {}).get("deliverable") or "") or findings
    manager = package.get("manager_decision") or ""
    verdict = evaluation.get("verdict") or ""
    if unresolved or (package.get("workflow_state") or "") == "UNRESOLVED":
        summary = "Unresolved. Approval did not create evidence, so this job is not complete."
    elif (package.get("workflow_state") or "") == "FAILED":
        summary = "Rejected. Nothing was sent."
    elif supported == 0 and (verdict == "PASS" or manager in {"ESCALATE", "CLARIFY", "PASS"}):
        summary = "No claim was supported. This is not a completed pass."
    else:
        summary = findings or "Recorded. See the findings below."
    return {
        "summary": _summary_clip(summary),
        "deliverable": deliverable,
        "findings": findings,
        "gaps": gaps[:6],
        "confidence": package.get("quality_score") if package.get("quality_score") is not None else "unknown",
        "evidence_status": trust,
        "manager": manager or evaluation.get("manager") or "",
        "time": _elapsed(package.get("created_at") or "", package.get("updated_at") or ""),
        "cost": cost,
    }
