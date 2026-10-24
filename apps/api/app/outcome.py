"""One answer to "how did this job end?", shared by Campus, Milo and the downloads.

A job's workflow state says where the package is. The outcome says what Lee may
do with the result. The two differ in ways that matter: a package Lee rejected and
a package whose step failed both sit in FAILED; a package marked COMPLETED with an
unmet acceptance criterion is not an accepted deliverable; and every result made
with simulated model responses says so.

States:
  COMPLETED            accepted; the deliverable may be used (check ``simulated``)
  NEEDS_APPROVAL       a draft for Lee to review; not accepted
  NEEDS_CLARIFICATION  waiting for Lee's answer
  IN_PROGRESS          still running
  BLOCKED              stopped until Lee acts (budget, unmet criteria, unresolved, stopped)
  REJECTED             Lee rejected it; diagnostic only
  FAILED               a step failed; diagnostic only
"""

from __future__ import annotations

import json

from .db import connect

SIMULATED_MODES = {"SIMULATED", "PARTLY_SIMULATED", "DEMO", "DRY_RUN"}
_IN_PROGRESS = {"", "DRAFT", "IN_PROGRESS", "UNDER_REVIEW", "REPAIRING", "READY", "APPROVED", "ACTIONING"}
_BLOCKING_KINDS = {"cap", "fallback", "access", "deadline", "rework"}

_NEXT = {
    "COMPLETED": "Download the deliverable.",
    "NEEDS_APPROVAL": "Review the draft, then approve or reject it with a reason.",
    "NEEDS_CLARIFICATION": "Answer the question so the job can continue.",
    "IN_PROGRESS": "Nothing yet. The job is still running.",
    "BLOCKED": "Read the reason and act on it before the job can finish.",
    "REJECTED": "Read the diagnostic report, then submit a new brief if you still want this.",
    "FAILED": "Read the diagnostic report, fix the cause, then retry the job.",
}


def _row(conn, sql: str, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def evidence_class(run_mode: str) -> str:
    """SIMULATED results never pass as live ones. Live configuration is still unverified."""
    mode = (run_mode or "").strip().upper()
    if mode in SIMULATED_MODES:
        return "SIMULATED"
    if mode == "REAL":
        return "LIVE"
    return "LIVE_UNVERIFIED"


def outcome_for(project_id: str = "", package_id: str = "") -> dict:
    """The outcome of a job, by project or by its top package. Never raises for a missing job."""
    conn = connect()
    try:
        package = None
        if package_id:
            package = _row(conn, "SELECT * FROM work_packages WHERE id=?", (package_id,))
            if package and package.get("parent_id"):
                package = _row(conn, "SELECT * FROM work_packages WHERE id=?", (package["parent_id"],)) or package
            project_id = project_id or ((package or {}).get("project_id") or "")
        project = _row(conn, "SELECT * FROM projects WHERE id=?", (project_id,)) if project_id else None
        if package is None and project_id:
            package = _row(
                conn,
                "SELECT * FROM work_packages WHERE project_id=? AND parent_id IS NULL ORDER BY updated_at DESC LIMIT 1",
                (project_id,),
            )
        rejected_by_lee = None
        override = None
        if project_id:
            rejected_by_lee = _row(
                conn,
                "SELECT id, reason FROM approvals WHERE project_id=? AND status='rejected' ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            )
            try:
                override = _row(
                    conn,
                    "SELECT id, reason FROM approvals WHERE project_id=? AND status='approved' AND accepted_unmet=1 "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project_id,),
                )
            except Exception:
                override = None  # a database from before the override column
    finally:
        conn.close()
    if project is None and package is None:
        return {"state": "UNKNOWN", "label": "UNKNOWN", "simulated": False, "evidence": "UNKNOWN",
                "accepted_deliverable": False, "draft_for_review": False, "diagnostic_only": False,
                "reason": "No such job.", "next_action": "", "criteria": {"met": 0, "not_met": 0, "unverified": 0}}

    package = package or {}
    project = project or {}
    run_mode = (project.get("run_mode") or "").strip() or "UNVERIFIED"
    evidence = evidence_class(run_mode)
    simulated = evidence == "SIMULATED"
    ws = (package.get("workflow_state") or "").upper()
    status = (project.get("status") or "").lower()

    criteria = {"met": 0, "not_met": 0, "unverified": 0}
    blockers: list[dict] = []
    if project_id:
        from .goals import criteria_for, list_blockers

        for item in criteria_for(project_id):
            key = item.get("status") or "unverified"
            criteria[key] = criteria.get(key, 0) + 1
        blockers = [b for b in list_blockers(project_id=project_id) if b.get("kind") in _BLOCKING_KINDS]

    reason = ""
    if ws == "FAILED" or (not ws and status in ("rejected", "failed")):
        if rejected_by_lee:
            state = "REJECTED"
            reason = "Lee rejected this result" + (f": {rejected_by_lee['reason']}" if rejected_by_lee.get("reason") else ".")
        else:
            state = "FAILED"
            reason = (package.get("error") or "").strip() or "A step failed, so nothing was delivered."
    elif status == "stopped":
        state, reason = "BLOCKED", "Lee stopped this job."
    elif ws == "UNRESOLVED" or status == "unresolved":
        state = "BLOCKED"
        reason = (package.get("next_action") or "").strip() or "Unresolved: the result is not supported by its evidence."
    elif ws == "AWAITING_CLARIFICATION":
        state, reason = "NEEDS_CLARIFICATION", (package.get("clarification_question") or "").strip()
    elif ws in ("AWAITING_APPROVAL", "ESCALATED"):
        state, reason = "NEEDS_APPROVAL", "A draft is waiting for Lee's decision. It is not accepted yet."
    elif ws == "COMPLETED":
        if criteria["not_met"] and override:
            state = "COMPLETED"
            reason = (f"Lee accepted it with {criteria['not_met']} unmet acceptance "
                      f"{'criterion' if criteria['not_met'] == 1 else 'criteria'}: {override.get('reason') or 'no reason given'}.")
        elif criteria["not_met"]:
            state = "BLOCKED"
            reason = (f"{criteria['not_met']} acceptance criterion is not met" if criteria["not_met"] == 1
                      else f"{criteria['not_met']} acceptance criteria are not met") + ", so the result is not accepted."
        else:
            state = "COMPLETED"
            reason = "Accepted." + (" Made with simulated model responses, not real work." if simulated else "")
    elif ws in _IN_PROGRESS:
        state, reason = "IN_PROGRESS", ""
    else:
        state, reason = "IN_PROGRESS", f"Workflow state {ws}."

    if state == "IN_PROGRESS" and blockers:
        state = "BLOCKED"
        reason = blockers[0].get("reason") or "Waiting for Lee."

    overridden = bool(state == "COMPLETED" and criteria["not_met"] and override)
    tags = []
    if overridden:
        tags.append("UNMET CRITERIA ACCEPTED BY LEE")
    if simulated:
        tags.append("SIMULATED")
    label = state + (f" ({'; '.join(tags)})" if tags else "")
    return {
        "state": state,
        "label": label,
        "simulated": simulated,
        "evidence": evidence,
        "run_mode": run_mode,
        "workflow_state": ws or None,
        "accepted_deliverable": state == "COMPLETED",
        "draft_for_review": state == "NEEDS_APPROVAL",
        "diagnostic_only": state in ("FAILED", "REJECTED", "BLOCKED"),
        "reason": reason[:400],
        "next_action": _NEXT.get(state, ""),
        "criteria": criteria,
        "criteria_overridden": overridden,
    }


def banner(outcome: dict) -> str:
    """The first lines of a downloaded deliverable.md. Empty only for a live, accepted result."""
    state = outcome.get("state") or "UNKNOWN"
    lines: list[str] = []
    if state == "COMPLETED":
        if outcome.get("criteria_overridden"):
            lines.append(f"> ACCEPTED WITH UNMET CRITERIA. {outcome.get('reason') or ''}".rstrip())
        if outcome.get("simulated"):
            lines.append("> SIMULATED. This result was made with simulated model responses. It is not real work.")
    elif state == "NEEDS_APPROVAL":
        lines.append("> DRAFT FOR REVIEW. Not accepted. Approve or reject it in Campus.")
    elif state in ("REJECTED", "FAILED", "BLOCKED"):
        lines.append(f"> {state}. NOT A DELIVERABLE. Diagnostic copy only.")
        if outcome.get("reason"):
            lines.append(f"> Reason: {outcome['reason']}")
    else:
        lines.append(f"> {state.replace('_', ' ')}. NOT A DELIVERABLE.")
    if state != "COMPLETED" and outcome.get("simulated"):
        lines.append("> SIMULATED. Made with simulated model responses.")
    return ("\n".join(lines) + "\n\n") if lines else ""


def download_name(package_id: str, outcome: dict, stem: str, ext: str) -> str:
    """bundle.zip keeps its name for an accepted live result. Anything else says what it is."""
    state = outcome.get("state") or "UNKNOWN"
    tags = []
    if state == "NEEDS_APPROVAL":
        tags.append("DRAFT")
    elif state != "COMPLETED":
        tags.append(state)
    if outcome.get("simulated"):
        tags.append("SIMULATED")
    if not tags:
        return f"{stem}.{ext}"
    return f"{stem}-{'-'.join(tags)}.{ext}"


def headers(outcome: dict) -> dict:
    return {
        "X-Ayven-Outcome": outcome.get("state") or "UNKNOWN",
        "X-Ayven-Evidence": outcome.get("evidence") or "UNKNOWN",
    }


def result_text(outcome: dict, findings: str) -> str:
    """The text Milo shows as the job's result. Rejected or failed work carries its label."""
    findings = findings or ""
    if outcome.get("state") in ("REJECTED", "FAILED", "BLOCKED"):
        head = f"{outcome['state']}. NOT A DELIVERABLE."
        if outcome.get("reason") and outcome["reason"] not in findings:
            head += f" {outcome['reason']}"
        return head + ("\n\n" + findings if findings else "")
    return findings


def to_json(outcome: dict) -> str:
    return json.dumps(outcome, sort_keys=True)
