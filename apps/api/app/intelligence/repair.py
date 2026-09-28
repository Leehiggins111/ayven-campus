"""Targeted repair after a material claim is rejected. The whole job is not rerun.

A repair searches, fetches, updates the ledger, rewrites the affected section,
and asks the supervisor check to look at the claim again. Attempts are capped.
"""

from __future__ import annotations

import json
import os
import re

from .boundary import reject_reasoning_query
from .claims import challenge, list_claims, mark_verified, now, update_claim

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

_EXPR = re.compile(r"\d+(?:\.\d+)?(?:\s*[\+\-\*/]\s*\d+(?:\.\d+)?)+")


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
    if "rewrite" in text:
        return "REWRITE"
    if "current availability" in text or "stale" in text:
        return "REPLACE_SOURCE"
    return "RESEARCH_MORE"


def repair_rate(actions: list[dict]) -> float | None:
    """Corrected rejected claims divided by rejected claims. A gap is not a correction."""
    rejected = [row for row in actions if row.get("action")]
    if not rejected:
        return None
    corrected = [row for row in rejected if row.get("resolved")]
    return round(len(corrected) / len(rejected), 3)


def apply_repairs(
    package_id: str,
    challenges: list[dict],
    *,
    cap: int | None = None,
    objective: str = "",
    agent_id: str = "research-e1",
    section: str = "",
    search_fn=None,
    fetch_fn=None,
) -> dict:
    """Execute a capped local repair. The affected section is rewritten. The job is not rerun."""
    limit = cap if cap is not None else int(os.environ.get("AYVEN_MAX_REPAIRS", "3"))
    actions = []
    evidence_added: list[dict] = []
    used = 0
    text = section or ""
    for row in challenges:
        kind = classify_repair(row)
        if not kind:
            continue
        if used >= limit:
            actions.append({
                "action": "UNRESOLVED_GAP",
                "claim_id": row.get("claim_id") or "",
                "detail": "repair cap reached",
                "resolved": False,
                "status": "UNVERIFIED",
            })
            continue
        outcome = _execute(
            package_id,
            row,
            kind,
            objective=objective,
            agent_id=agent_id,
            section=text,
            search_fn=search_fn,
            fetch_fn=fetch_fn,
        )
        text = outcome.get("section", text)
        evidence_added.extend(outcome.get("evidence") or [])
        actions.append({key: value for key, value in outcome.items() if key != "evidence"})
        used += 1
    return {
        "actions": actions,
        "attempted": used,
        "retries": used,
        "capped": limit,
        "section": text,
        "evidence": evidence_added,
        "repair_rate": repair_rate(actions),
    }


def _execute(package_id, row, kind, *, objective, agent_id, section, search_fn, fetch_fn) -> dict:
    claim_id = row.get("claim_id") or ""
    claim = _claim(package_id, claim_id)
    claim_text = (claim or {}).get("claim_text") or (row.get("evidence") or "")
    detail = (row.get("resolution") or kind)[:300]
    evidence: list[dict] = []
    status = "UNVERIFIED"
    resolved = False
    recheck = ""

    if kind == "RECALCULATE":
        expression = _expression(row, claim_text)
        if expression:
            from .calc import CalcError, eval_arithmetic

            try:
                value = eval_arithmetic(expression)
            except CalcError as exc:
                detail = f"calculator refused the expression: {exc}"
                kind = "UNRESOLVED_GAP"
            else:
                detail = f"{expression} = {value}"
                if claim_id:
                    update_claim(
                        claim_id,
                        claim_text=f"Calculator result {value} from {expression}.",
                        evidence_text=detail,
                        source_type="DETERMINISTIC",
                        source_url="",
                        freshness="INPUT",
                        status="SUPPORTED",
                        confidence=0.9,
                        authority="DETERMINISTIC",
                        origin="repair",
                        verification_status="CHECKED",
                    )
                    _note(claim_id, "RECALCULATE", detail)
                status = "SUPPORTED"
                resolved = True
                section = rewrite_section(section, claim_text, f"Recalculated: {detail}.")
                recheck = "calculator"
        else:
            kind = "UNRESOLVED_GAP"
            detail = "No arithmetic expression was available to recalculate. Model memory was not used."
            if claim_id:
                challenge(claim_id, "repair", detail, "UNVERIFIED")
                mark_verified(claim_id, "UNVERIFIED")
                _note(claim_id, "RECALCULATE", detail)
    elif kind in ("RESEARCH_MORE", "REPLACE_SOURCE"):
        question = _targeted_question(row) or _targeted_question({"resolution": claim_text, "evidence": ""})
        if not question or reject_reasoning_query(question):
            kind = "UNRESOLVED_GAP"
            detail = "No safe follow-up query. Model memory was not used."
            if claim_id:
                challenge(claim_id, "repair", detail, "UNVERIFIED")
                mark_verified(claim_id, "UNVERIFIED")
                _note(claim_id, kind, detail)
        else:
            found = _research(package_id, agent_id, objective or question, question, search_fn, fetch_fn)
            evidence = found.get("evidence") or []
            support = _supporting(row.get("follow_up") or claim_text, evidence)
            if support:
                passage = (support.get("extracted_content") or "")[:500]
                meta = support.get("metadata") or {}
                replacement = passage if row.get("follow_up") else claim_text
                if claim_id:
                    update_claim(
                        claim_id,
                        claim_text=replacement[:500],
                        evidence_text=passage,
                        source_url=support.get("source_url") or "",
                        source_type=meta.get("source_rank") or "UNKNOWN",
                        freshness=meta.get("freshness") or "UNKNOWN",
                        status="SUPPORTED",
                        confidence=0.7,
                        authority=meta.get("authority_class") or meta.get("source_rank") or "",
                        origin="repair",
                        verification_status="CHECKED",
                    )
                    recheck_row = _recheck(package_id, claim_id, passage)
                    recheck = recheck_row.get("result") or ""
                else:
                    recheck = "STOOD"
                if recheck == "DISPROVED":
                    if claim_id:
                        challenge(claim_id, "repair", "Supervisor recheck still disproved the repaired claim.", "UNVERIFIED")
                        mark_verified(claim_id, "UNVERIFIED")
                    status = "UNVERIFIED"
                    resolved = False
                    kind = "UNRESOLVED_GAP"
                    detail = "Follow-up page did not survive the supervisor recheck."
                else:
                    status = "SUPPORTED"
                    resolved = True
                    detail = question
                    section = rewrite_section(section, claim_text, passage)
                if claim_id:
                    _note(claim_id, "RESEARCH_MORE" if kind != "UNRESOLVED_GAP" else "UNRESOLVED_GAP", detail)
            else:
                if claim_id:
                    challenge(claim_id, "repair", f"{kind}: no supporting page for {question}", "UNVERIFIED")
                    mark_verified(claim_id, "UNVERIFIED")
                    _note(claim_id, kind, question)
                kind = "UNRESOLVED_GAP"
                detail = f"Research ran for '{question}' and no supporting page was opened."
                status = "UNVERIFIED"
    elif kind == "REMOVE_CLAIM":
        if claim_id:
            challenge(claim_id, "repair", f"REMOVE_CLAIM: {detail}", "UNVERIFIED")
            mark_verified(claim_id, "UNVERIFIED")
            _note(claim_id, kind, detail)
        section = rewrite_section(section, claim_text, "")
        status = "UNVERIFIED"
        resolved = True
        recheck = "removed"
    elif kind == "DOWNGRADE_TO_INFERENCE":
        if claim_id:
            update_claim(claim_id, claim_type="INFERENCE", status="PARTIALLY_SUPPORTED", origin="repair")
            challenge(claim_id, "repair", detail, "PARTIALLY_SUPPORTED")
            _note(claim_id, kind, detail)
        section = rewrite_section(section, claim_text, f"Inference, not a fact: {claim_text}")
        status = "PARTIALLY_SUPPORTED"
        resolved = True
        recheck = "downgraded"
    elif kind == "REWRITE":
        if claim_id:
            challenge(claim_id, "repair", detail, "UNVERIFIED")
            mark_verified(claim_id, "UNVERIFIED")
            _note(claim_id, kind, detail)
        section = rewrite_section(section, claim_text, "The unsupported sentence was removed from this section.")
        status = "UNVERIFIED"
        resolved = True
        recheck = "rewritten"
    elif kind == "ASK_CLARIFICATION":
        if claim_id:
            challenge(claim_id, "repair", detail, "UNVERIFIED")
            mark_verified(claim_id, "UNVERIFIED")
            _note(claim_id, kind, detail)
        status = "UNVERIFIED"
        resolved = False
    else:
        if claim_id:
            challenge(claim_id, "repair", detail, "UNVERIFIED")
            mark_verified(claim_id, "UNVERIFIED")
            _note(claim_id, kind, detail)

    return {
        "action": kind,
        "claim_id": claim_id,
        "detail": detail[:300],
        "resolved": resolved,
        "status": status,
        "recheck": recheck,
        "section": section,
        "evidence": evidence,
    }


def rewrite_section(section: str, claim_text: str, replacement: str) -> str:
    """Rewrite only the sentences that carry the rejected claim."""
    source = section or ""
    if not source and not replacement:
        return ""
    needle = " ".join((claim_text or "").split()[:8]).strip()
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", source):
        if needle and needle[:40] and needle[:40].lower() in sentence.lower():
            continue
        if sentence.strip():
            kept.append(sentence.strip())
    if replacement and replacement.strip():
        kept.append(replacement.strip())
    return " ".join(kept).strip()


def _research(package_id, agent_id, objective, question, search_fn, fetch_fn) -> dict:
    from .research import research

    return research(
        "web_research",
        objective,
        package_id,
        agent_id,
        queries=[question],
        max_rounds=1,
        search_fn=search_fn,
        fetch_fn=fetch_fn,
    )


def _supporting(claim_text: str, evidence: list[dict]) -> dict | None:
    wanted = _tokens(claim_text)
    best = None
    best_score = 0
    for item in evidence:
        passage = item.get("extracted_content") or ""
        overlap = wanted & _tokens(passage)
        if len(overlap) > best_score:
            best = item
            best_score = len(overlap)
    if best is None or best_score < 2:
        return None
    return best


def _recheck(package_id: str, claim_id: str, passage: str) -> dict:
    from .audit import challenge_material_claims

    claim = _claim(package_id, claim_id)
    if not claim:
        return {}
    rows = challenge_material_claims([claim], passage)
    return rows[0] if rows else {}


def _expression(row: dict, claim_text: str) -> str:
    blob = " ".join([
        row.get("challenge") or "",
        row.get("resolution") or "",
        row.get("evidence") or "",
        claim_text or "",
    ])
    match = _EXPR.search(blob)
    return re.sub(r"\s+", "", match.group(0)) if match else ""


def _tokens(text: str) -> set[str]:
    return {part for part in re.findall(r"[a-z0-9]{4,}", (text or "").lower())}


def _claim(package_id: str, claim_id: str) -> dict | None:
    if not claim_id:
        return None
    for claim in list_claims(package_id):
        if claim.get("id") == claim_id:
            return claim
    return None


def _targeted_question(row: dict) -> str:
    follow = (row.get("follow_up") or "").strip()
    if follow:
        if reject_reasoning_query(follow):
            return ""
        return follow[:160]
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
