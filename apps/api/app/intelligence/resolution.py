"""Manager resolution. The decision is chosen after a reason, not instead of one."""

from __future__ import annotations


def resolve_manager(
    *,
    task_class: str,
    audits: list[dict],
    claims: list[dict],
    quote: dict | None,
    research: dict | None,
    advisory: str = "",
) -> dict:
    research = research or {}
    audits = audits or []
    claims = claims or []
    supervisor_decisions = [audit.get("decision") or "" for audit in audits]
    employee_view = advisory or "employee published the ledger draft"
    supervisor_view = ", ".join(f"{audit.get('focus')}={audit.get('decision')}" for audit in audits) or "no audit"
    conflict = any(audit.get("advisory") and audit.get("advisory") != audit.get("decision") for audit in audits)
    contradicted = [c for c in claims if c.get("status") == "CONTRADICTED" and not c.get("supersedes")]
    calc_only = bool(contradicted) and all(c.get("claim_type") == "CALCULATION" for c in contradicted)
    evidence = research.get("evidence") or []
    exhausted = bool(research.get("rounds_exhausted", True))
    gaps = research.get("gaps") or []

    if any(decision == "ESCALATE" for decision in supervisor_decisions):
        method = "unresolved"
        decision = "ESCALATE"
        reason = "The supervisor escalated. A local rewrite does not settle it, and no frontier model is called."
    elif contradicted and not calc_only:
        method = "unresolved"
        decision = "ESCALATE"
        reason = "Employee and source material contradict each other. Another page was not available to settle it, and the calculator does not apply."
    elif calc_only:
        method = "deterministic_tool"
        decision = "CLARIFY" if task_class not in ("trivial", "calculation") else "SYNTHESISE"
        reason = "The calculator recomputed the disputed total. The deterministic result replaces the model figure."
    elif quote and quote.get("labour_unit") == "AMBIGUOUS":
        method = "customer_clarification"
        decision = "CLARIFY"
        reason = "Both labour readings are arithmetically valid. No page or tool can prove whether labour is per door or per job."
    elif task_class in ("football_tickets", "vending_prospects", "internal_door_quote"):
        method = "customer_clarification" if task_class != "football_tickets" or evidence else "research_pass"
        if task_class == "football_tickets" and not evidence and not exhausted:
            method = "research_pass"
            decision = "CLARIFY"
            reason = "No page was opened and a further research pass could still run. Contact stays unsent."
        elif not evidence and exhausted and task_class != "internal_door_quote":
            method = "unresolved"
            decision = "ESCALATE"
            reason = "Research finished without an opened page. Model memory was not used to fill the gap."
        else:
            method = "evidence" if evidence or quote else "customer_clarification"
            if gaps and evidence:
                method = "evidence"
            decision = "CLARIFY"
            reason = "The opened evidence can support the briefing. Sending, purchasing, or treating a figure as a customer quote still needs a person."
    elif not evidence and gaps and exhausted:
        method = "unresolved"
        decision = "ESCALATE"
        reason = "Insufficient evidence after the research budget. Nothing was invented to fill it."
    else:
        method = "evidence"
        decision = "SYNTHESISE"
        reason = "The evidence and the checks agree far enough to publish. No external action is requested."

    if conflict and decision == "SYNTHESISE":
        reason += " Where the employee and the supervisor disagreed, the authoritative check won."

    return {
        "employee_interpretation": employee_view,
        "supervisor_interpretation": supervisor_view,
        "conflict": conflict,
        "resolution_method": method,
        "decision": decision,
        "reason": reason,
        "research_exhausted": exhausted,
    }
