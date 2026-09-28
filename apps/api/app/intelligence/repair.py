"""Targeted repair after a material claim is rejected. The whole job is not rerun."""

from __future__ import annotations

import os

from .boundary import reject_reasoning_query
from .claims import challenge, list_claims, mark_verified

ACTIONS = (
    "RESEARCH_MORE",
    "REPLACE_SOURCE",
    "RECALCULATE",
    "REMOVE_CLAIM",
    "DOWNGRADE_TO_INFERENCE",
    "ASK_CLARIFICATION",
    "REWRITE",
    "EMPLOYEE_RETRY",
    "UNRESOLVED_GAP",
)


def classify_repair(challenge_row: dict) -> str:
    text = " ".join([
        challenge_row.get("challenge") or "",
        challenge_row.get("resolution") or "",
        challenge_row.get("result") or "",
    ]).lower()
    if challenge_row.get("result") not in ("DISPROVED", "UNSUPPORTED"):
        return ""
    if "calculator" in text or "arithmetic" in text or "pence" in text:
        return "RECALCULATE"
    if "url" in text and "opened" in text:
        return "REMOVE_CLAIM"
    if "footfall" in text or "not in the evidence" in text or "figure" in text:
        return "REMOVE_CLAIM"
    if "inference" in text:
        return "DOWNGRADE_TO_INFERENCE"
    if "current availability" in text or "stale" in text:
        return "REPLACE_SOURCE"
    return "RESEARCH_MORE"


def apply_repairs(package_id: str, challenges: list[dict], *, cap: int | None = None) -> dict:
    """Execute a capped local repair. Successful removals do not stay published as supported facts."""
    limit = cap if cap is not None else int(os.environ.get("AYVEN_MAX_REPAIRS", "3"))
    actions = []
    used = 0
    for row in challenges:
        kind = classify_repair(row)
        if not kind:
            continue
        if used >= limit:
            actions.append({"action": "UNRESOLVED_GAP", "claim_id": row.get("claim_id") or "", "detail": "repair cap reached"})
            continue
        claim_id = row.get("claim_id") or ""
        detail = (row.get("resolution") or kind)[:300]
        if kind == "RESEARCH_MORE":
            question = _targeted_question(row)
            if not question or reject_reasoning_query(question):
                kind = "UNRESOLVED_GAP"
                detail = "No safe follow-up query. Model memory was not used."
            else:
                detail = question
        if claim_id and kind in ("REMOVE_CLAIM", "REPLACE_SOURCE", "RECALCULATE", "UNRESOLVED_GAP", "RESEARCH_MORE"):
            challenge(claim_id, "repair", f"{kind}: {detail}", "UNVERIFIED")
            mark_verified(claim_id, "UNVERIFIED")
            _note(claim_id, kind, detail)
        elif claim_id and kind == "DOWNGRADE_TO_INFERENCE":
            challenge(claim_id, "repair", detail, "PARTIALLY_SUPPORTED")
            _note(claim_id, kind, detail)
        actions.append({"action": kind, "claim_id": claim_id, "detail": detail})
        used += 1
    return {"actions": actions, "attempted": used, "capped": limit}


def _targeted_question(row: dict) -> str:
    evidence = (row.get("evidence") or "").strip()
    resolution = (row.get("resolution") or "").strip()
    text = resolution or evidence
    if reject_reasoning_query(text):
        return ""
    words = text.split()
    if len(words) < 3:
        return ""
    return " ".join(words[:12])[:160]


def _note(claim_id: str, action: str, detail: str) -> None:
    from ..db import connect
    import json
    from .claims import now

    conn = connect()
    row = conn.execute("SELECT repair_history FROM claims WHERE id=?", (claim_id,)).fetchone()
    if row is None:
        conn.close()
        return
    try:
        history = json.loads(row["repair_history"] or "[]")
    except json.JSONDecodeError:
        history = []
    history.append({"action": action, "detail": detail[:240], "at": now()})
    conn.execute("UPDATE claims SET repair_history=?, updated_at=? WHERE id=?", (json.dumps(history), now(), claim_id))
    conn.commit()
    conn.close()


def repaired_claims(package_id: str) -> list[dict]:
    rows = []
    for claim in list_claims(package_id):
        history = claim.get("repair_history") or ""
        if history and history not in ("", "[]", "null"):
            rows.append(claim)
    return rows
