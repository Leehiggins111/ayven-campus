"""Operational quality signal. Not a model self-score and not an objective truth."""

from __future__ import annotations

_WEIGHTS = {
    "coverage": 0.16,
    "source_quality": 0.14,
    "claim_support": 0.18,
    "freshness": 0.1,
    "contradictions": 0.1,
    "unknowns": 0.12,
    "calculation_checks": 0.1,
    "supervisor_findings": 0.05,
    "verification_pass_rate": 0.05,
}
_RANK = {"PRIMARY_OFFICIAL": 1.0, "DETERMINISTIC": 1.0, "INPUT": 0.9, "HIGH_QUALITY_SECONDARY": 0.75, "OTHER_SECONDARY": 0.45, "COMMUNITY": 0.2, "UNKNOWN": 0.1}


def score(task_class: str, claims: list[dict], research: dict, verification: dict, supervisor_decision: str, quote: dict | None) -> dict:
    material = [c for c in claims if c["claim_type"] not in ("MISSING_INFORMATION",)]
    supported = [c for c in material if c["status"] in ("SUPPORTED", "PARTIALLY_SUPPORTED")]
    claim_support = (len(supported) / len(material)) if material else 0.0
    ranks = [_RANK.get(c.get("source_type") or "", 0.1) for c in supported] or [0.0]
    source_quality = sum(ranks) / len(ranks)
    fresh_vals = []
    for claim in supported:
        freshness = claim.get("freshness")
        if freshness == "LIVE":
            fresh_vals.append(1.0)
        elif freshness in ("INPUT",):
            fresh_vals.append(0.95)
        elif freshness == "FIXTURE_SNAPSHOT":
            fresh_vals.append(0.45)
        elif freshness == "STALE":
            fresh_vals.append(0.1)
        else:
            fresh_vals.append(0.3)
    freshness = sum(fresh_vals) / len(fresh_vals) if fresh_vals else 0.4
    contradictions = 0.0 if any(c["status"] == "CONTRADICTED" for c in claims) else 1.0
    unknowns_listed = any(c["claim_type"] == "MISSING_INFORMATION" for c in claims) or bool(research.get("gaps"))
    needs_unknowns = task_class in ("internal_door_quote", "football_tickets", "vending_prospects", "web_research", "business_research")
    unknowns = 1.0 if (unknowns_listed or not needs_unknowns) else 0.2
    if task_class == "internal_door_quote":
        calculation_checks = 1.0 if verification.get("arithmetic_ok") and quote and quote.get("labour_unit") == "AMBIGUOUS" else 0.0
        facets = [
            quote is not None,
            bool(quote and quote["scenarios"]["labour_per_door"]["total_ex_vat"]),
            bool(quote and quote["scenarios"]["labour_per_job"]["total_ex_vat"]),
            bool(quote and quote["labour_unit"] == "AMBIGUOUS"),
            bool(quote and quote["vat"] == "UNKNOWN_NOT_APPLIED"),
            unknowns_listed,
        ]
        coverage = sum(1 for item in facets if item) / len(facets)
    elif task_class == "football_tickets":
        calculation_checks = 1.0
        urls = [c.get("source_url") for c in claims if c.get("source_url")]
        coverage = 1.0 if urls else 0.35
    elif task_class == "vending_prospects":
        calculation_checks = 1.0
        coverage = 0.8 if unknowns_listed else 0.3
    else:
        calculation_checks = 1.0 if task_class != "calculation" else (1.0 if verification.get("arithmetic_ok", True) else 0.0)
        coverage = 0.7 if (supported or research.get("skipped")) else 0.3
    supervisor_findings = 1.0 if supervisor_decision in ("ACCEPT", "TAKE_OVER") else 0.4 if supervisor_decision == "RETURN" else 0.2
    parts = {
        "coverage": round(coverage, 2),
        "source_quality": round(source_quality, 2),
        "claim_support": round(claim_support, 2),
        "freshness": round(freshness, 2),
        "contradictions": contradictions,
        "unknowns": unknowns,
        "calculation_checks": calculation_checks,
        "supervisor_findings": supervisor_findings,
        "verification_pass_rate": verification.get("pass_rate", 0),
    }
    overall = round(sum(parts[k] * _WEIGHTS[k] for k in _WEIGHTS), 2)
    return {
        **parts,
        "overall": overall,
        "note": "Operational signal from evidence and checks. Not an objective measure of truth.",
    }
