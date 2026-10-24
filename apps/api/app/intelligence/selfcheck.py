"""Structured employee self-check. It is a checklist, not a chain of thought."""

from __future__ import annotations

import re

from .schemas import EmployeeSelfCheck


def self_check(*, objective: str, report: str, claims: list[dict], research: dict | None = None, quote: dict | None = None) -> dict:
    research = research or {}
    opened = {item.get("source_url") or "" for item in research.get("evidence") or []}
    lacking = []
    inferences = []
    urls_closed = []
    orgs = []
    contradictions = []
    for claim in claims:
        text = claim.get("claim_text") or ""
        if claim.get("claim_type") == "INFERENCE" or text.upper().startswith("INFERENCE"):
            inferences.append(text[:160])
        if claim.get("status") in ("UNVERIFIED", "CONTRADICTED") and claim.get("claim_type") not in ("MISSING_INFORMATION", "SOURCE_FAILURE"):
            lacking.append(text[:160])
        for url in re.findall(r"https?://[^\s)>\]]+", text):
            if url.rstrip(".,") not in opened and url.rstrip(".,") not in (claim.get("evidence_text") or ""):
                urls_closed.append(url.rstrip(".,"))
        if claim.get("status") == "CONTRADICTED":
            contradictions.append(text[:160])
    gaps = list(research.get("gaps") or [])
    numbers_ok = True
    if quote:
        door = str(quote["scenarios"]["labour_per_door"]["total_ex_vat"])
        job = str(quote["scenarios"]["labour_per_job"]["total_ex_vat"])
        numbers_ok = door in (report or "") and job in (report or "")
    answered = bool((report or "").strip()) and (not gaps or "gap" in (report or "").lower() or "missing" in (report or "").lower() or "unknown" in (report or "").lower())
    result = EmployeeSelfCheck(
        objective_answered=answered,
        missing_items=gaps[:8],
        claims_lacking_evidence=lacking[:8],
        inferences=inferences[:8],
        numbers_from_calculator=numbers_ok,
        urls_not_opened=urls_closed[:8],
        organisations_not_evidenced=orgs,
        contradictions=contradictions[:8],
        more_research_needed=bool(gaps) or bool(lacking),
    )
    payload = result.model_dump()
    payload["objective_present"] = bool((objective or "").strip())
    return payload
