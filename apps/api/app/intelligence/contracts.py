"""Machine-readable completion contract for a work package."""

from __future__ import annotations


def contract_for(task_class: str, plan: dict | None = None) -> dict:
    plan = plan or {}
    external = task_class not in ("trivial", "calculation")
    return {
        "task_class": task_class,
        "required_deliverables": [plan.get("deliverable") or "source-backed briefing"],
        "evidence_classes": ["opened_page"] if "research" in (plan.get("stages") or []) else ["deterministic"],
        "minimum_useful_findings": 0 if task_class == "trivial" else 1,
        "calculations": list(plan.get("calculations") or []),
        "unknowns_to_surface": list(plan.get("unknowns") or []),
        "prohibited_assumptions": [
            "model memory is not evidence",
            "an unopened URL is not a source",
            "a recommendation is not a fact",
        ],
        "action_permissions": {
            "external_contact": False,
            "purchase": False,
            "send": False,
            "requires_human_approval": external or bool(plan.get("human_approval_required")),
        },
    }


def evaluate_contract(contract: dict, *, report: str, research: dict | None = None, claims: list[dict] | None = None) -> dict:
    research = research or {}
    claims = claims or []
    evidence = research.get("evidence") or []
    text = report or ""
    deliverable_ok = bool(text.strip())
    if "opened_page" in contract.get("evidence_classes", []):
        evidence_ok = bool(evidence) or bool(research.get("skipped"))
    else:
        evidence_ok = True
    useful = 1 if deliverable_ok and (evidence or research.get("skipped") or claims) else 0
    minimum = int(contract.get("minimum_useful_findings") or 0)
    unresolved = [item for item in (research.get("gaps") or []) if item]
    prohibited_hit = []
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered:
        opened = {item.get("source_url") or "" for item in evidence}
        for url in _urls(text):
            if url not in opened and url not in " ".join(claim.get("evidence_text") or "" for claim in claims):
                prohibited_hit.append("unopened URL in the deliverable")
                break
    safety_ok = "nothing was sent" in lowered or "sent: no" in lowered or not contract["action_permissions"]["requires_human_approval"]
    coverage = 1.0 if deliverable_ok and evidence_ok else 0.5 if deliverable_ok else 0.0
    passed = deliverable_ok and evidence_ok and useful >= minimum and safety_ok and not prohibited_hit
    return {
        "passed": passed,
        "coverage": coverage,
        "usefulness": useful,
        "safety_ok": safety_ok,
        "unresolved_required": unresolved[:8],
        "prohibited": prohibited_hit,
        "correctness_boundary": "ledger and calculator, not model prose",
    }


def _urls(text: str) -> list[str]:
    import re
    return [item.rstrip(".,") for item in re.findall(r"https?://[^\s)>\]]+", text or "")]
