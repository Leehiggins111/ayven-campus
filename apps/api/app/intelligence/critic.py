"""Critic: what might be wrong. This stage does not fix the work."""

from __future__ import annotations

from .research import is_availability_text


def critique(task_class: str, report: str, claims: list[dict], research: dict, quote: dict | None) -> dict:
    assumptions = []
    unanswered = []
    weak = []
    inconsistencies = []
    drift = []
    for claim in claims:
        if claim["claim_type"] == "ASSUMPTION" or claim["status"] == "PARTIALLY_SUPPORTED":
            assumptions.append(claim["claim_text"][:240])
        if claim["status"] in ("UNVERIFIED", "STALE", "CONTRADICTED"):
            weak.append({"id": claim["id"], "status": claim["status"], "text": claim["claim_text"][:240]})
        if claim["claim_type"] == "MISSING_INFORMATION":
            unanswered.append(claim["claim_text"][:240])
    for gap in research.get("gaps") or []:
        unanswered.append(gap)
    for failure in research.get("failures") or []:
        unanswered.append(f"Retrieval failure: {failure.get('stage')} {failure.get('error')}")
    if quote and quote.get("labour_unit") == "AMBIGUOUS":
        assumptions.append("Labour unit is ambiguous and must stay unresolved.")
    if quote and "final quote is" in report.lower() and "not a final quote" not in report.lower():
        inconsistencies.append("Report language treats a scenario total as a final quote.")
    if any(is_availability_text(c["claim_text"]) and c["status"] != "STALE" and c["freshness"] != "LIVE" for c in claims):
        inconsistencies.append("Availability language is not backed by a live retrieval.")
    if task_class == "vending_prospects" and "sent: no" not in report.lower() and "nothing was sent" not in report.lower():
        drift.append("Vending output must show that no contact was sent.")
    return {
        "stage": "critic",
        "assumptions": assumptions[:12],
        "unanswered": unanswered[:12],
        "weak_claims": weak[:12],
        "inconsistencies": inconsistencies,
        "drift": drift,
    }
