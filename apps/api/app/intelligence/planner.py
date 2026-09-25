"""Structured plan produced before an employee acts."""

from __future__ import annotations

import os


def classify(objective: str) -> str:
    text = objective.lower().strip()
    if any(key in text for key in ("internal door", "hinges")):
        return "internal_door_quote"
    if "vending" in text:
        return "vending_prospects"
    if "ticket" in text:
        return "football_tickets"
    if any(key in text for key in ("calculate", "what is the sum", "arithmetic")):
        return "calculation"
    if len(text) < 24 and not any(key in text for key in ("price", "current", "latest", "who", "research")):
        return "trivial"
    if any(key in text for key in ("prospect", "supplier", "market", "business")):
        return "business_research"
    return "web_research"


def child_plan(task_class: str) -> list[dict]:
    if task_class == "internal_door_quote":
        return [
            {"agent_id": "research-e1", "title": "Provisional door scenarios", "focus": "scenarios"},
            {"agent_id": "research-e2", "title": "Measurement and specification gaps", "focus": "gaps"},
            {"agent_id": "research-e3", "title": "Supplier evidence", "focus": "evidence"},
        ]
    if task_class == "football_tickets":
        return [
            {"agent_id": "research-e1", "title": "Official ticket routes", "focus": "routes"},
            {"agent_id": "research-e2", "title": "Official versus reseller", "focus": "channels"},
            {"agent_id": "research-e3", "title": "Enquiry gaps", "focus": "gaps"},
        ]
    if task_class == "vending_prospects":
        return [
            {"agent_id": "research-e1", "title": "Evidenced placement prospects", "focus": "prospects"},
            {"agent_id": "research-e2", "title": "Decision-maker and missing information", "focus": "gaps"},
            {"agent_id": "research-e3", "title": "Outreach draft only", "focus": "draft"},
        ]
    if task_class == "trivial":
        return [{"agent_id": "research-e1", "title": "Short acknowledgement", "focus": "trivial"}]
    if task_class == "calculation":
        return [{"agent_id": "research-e1", "title": "Deterministic calculation", "focus": "scenarios"}]
    return [
        {"agent_id": "research-e1", "title": "Collect source evidence", "focus": "evidence"},
        {"agent_id": "research-e2", "title": "List unknowns", "focus": "gaps"},
        {"agent_id": "research-e3", "title": "Next action", "focus": "draft"},
    ]


def build_plan(objective: str, task_class: str, skill_names: list[str]) -> dict:
    rounds = int(os.environ.get("AYVEN_MAX_RESEARCH_ROUNDS", "2"))
    attempts = int(os.environ.get("AYVEN_MAX_ATTEMPTS", "2"))
    skipped = []
    stages = ["classify", "plan", "skills", "model_route", "tool_select"]
    if task_class == "trivial":
        stages += ["draft", "supervisor", "manager"]
        skipped = ["research", "browser", "calculation", "claim_depth"]
    elif task_class == "calculation":
        stages += ["calculation", "claims", "draft", "critic", "verify", "supervisor", "manager"]
        skipped = ["research", "browser"]
    elif task_class == "internal_door_quote":
        stages += ["calculation", "research", "evidence", "claims", "draft", "critic", "verify", "revise", "supervisor", "manager", "approval"]
    else:
        stages += ["research", "evidence", "claims", "draft", "critic", "verify", "revise", "supervisor", "manager", "approval"]
        skipped = ["calculation"]
    factual = {
        "internal_door_quote": ["both labour scenarios", "VAT left unknown", "missing specification fields", "no unnamed supplier"],
        "football_tickets": ["official route where a page was opened", "reseller not treated as authorised", "no live stock"],
        "vending_prospects": ["prospects only from opened pages", "outreach remains a draft"],
        "trivial": ["no current-facts claim"],
    }.get(task_class, ["claims limited to retrieved evidence"])
    current = {
        "football_tickets": ["live ticket availability only from a live page that states it"],
        "vending_prospects": ["current notices only from pages that actually list them"],
        "internal_door_quote": ["supplier prices only from a retrieved page"],
    }.get(task_class, [])
    tools = []
    if "calculation" in stages:
        tools.append("calculator")
    if "research" in stages:
        tools.extend(["web_search", "fetch_page"])
    from .capabilities import select_tools

    tools = select_tools(tools)
    approval = task_class not in ("trivial", "calculation")
    return {
        "objective": objective,
        "task_class": task_class,
        "deliverable": _deliverable(task_class),
        "factual_requirements": factual,
        "current_data_requirements": current,
        "unknowns": [],
        "required_tools": tools,
        "required_skills": skill_names,
        "calculations": ["internal door scenarios"] if task_class == "internal_door_quote" else [],
        "browsing": False,
        "evidence_requirements": ["opened page, not a snippet, for any external fact", "deterministic tool for arithmetic"],
        "risk": "high" if approval else "low",
        "human_approval_required": approval,
        "expected_output_format": "markdown briefing with claims, gaps, and an explicit approval state",
        "stages": stages,
        "skipped_stages": skipped,
        "children": child_plan(task_class),
        "budget": {"max_research_rounds": rounds, "max_attempts": attempts, "max_manager_loops": int(os.environ.get("AYVEN_MAX_MANAGER_LOOPS", "1"))},
    }


def _deliverable(task_class: str) -> str:
    return {
        "internal_door_quote": "Provisional scenarios and a list of what must be confirmed before any customer quote",
        "football_tickets": "Evidenced official routes, reseller distinction, and enquiry gaps. No purchase.",
        "vending_prospects": "Prospects only where a source supports them, plus an unsent outreach draft",
        "trivial": "Short acknowledgement with no invented facts",
        "calculation": "Deterministic arithmetic with the expression shown",
    }.get(task_class, "Source-backed briefing with explicit gaps")
