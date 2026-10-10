"""Deterministic work-package states. Transitions are explicit and logged."""

from __future__ import annotations

STATES = (
    "DRAFT",
    "IN_PROGRESS",
    "UNDER_REVIEW",
    "REPAIRING",
    "READY",
    "AWAITING_CLARIFICATION",
    "AWAITING_APPROVAL",
    "APPROVED",
    "ACTIONING",
    "COMPLETED",
    "UNRESOLVED",
    "FAILED",
    "ESCALATED",
)

_ALLOWED = {
    "DRAFT": {"IN_PROGRESS", "FAILED"},
    "IN_PROGRESS": {"UNDER_REVIEW", "REPAIRING", "FAILED", "AWAITING_CLARIFICATION"},
    "UNDER_REVIEW": {"REPAIRING", "READY", "FAILED", "ESCALATED"},
    "REPAIRING": {"UNDER_REVIEW", "READY", "FAILED", "AWAITING_CLARIFICATION"},
    "READY": {"AWAITING_CLARIFICATION", "AWAITING_APPROVAL", "COMPLETED", "ESCALATED", "FAILED", "REPAIRING"},
    "AWAITING_CLARIFICATION": {"IN_PROGRESS", "FAILED", "ESCALATED"},
    "AWAITING_APPROVAL": {"APPROVED", "FAILED", "ESCALATED"},
    "APPROVED": {"ACTIONING", "COMPLETED"},
    "ACTIONING": {"COMPLETED", "UNRESOLVED", "FAILED"},
    "COMPLETED": set(),
    "UNRESOLVED": set(),
    "FAILED": set(),
    "ESCALATED": {"AWAITING_APPROVAL"},
}


def transition(current: str, new: str) -> str:
    current = current or "DRAFT"
    if current not in STATES or new not in STATES:
        raise ValueError(f"unknown workflow state {current}->{new}")
    if new == current:
        return current
    if new not in _ALLOWED[current]:
        raise ValueError(f"illegal workflow transition {current}->{new}")
    return new


def state_for_manager(decision: str, *, approval_required: bool) -> str:
    if decision == "ESCALATE":
        return "ESCALATED"
    if decision in ("CLARIFY", "APPROVAL_REQUIRED") or approval_required and decision in ("RETURN", "RESEARCH_MORE", "SYNTHESISE", "ACCEPT"):
        return "AWAITING_APPROVAL"
    if decision in ("SYNTHESISE", "ACCEPT"):
        return "COMPLETED"
    if decision in ("REPAIR", "RESEARCH_MORE", "RETURN"):
        return "REPAIRING"
    return "READY"


def log_transition(package_id: str, new: str, reason: str) -> str:
    from ..distribution import update_package
    from .store import package, save_verification

    row = package(package_id) or {}
    current = row.get("workflow_state") or "DRAFT"
    try:
        landed = transition(current, new)
    except ValueError:
        landed = new if new in STATES else current
        reason = f"{reason} (forced from {current})"
    update_package(package_id, workflow_state=landed)
    save_verification(package_id, "workflow", "control", landed, {"from": current, "to": landed, "reason": reason[:300]})
    return landed
