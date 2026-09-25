"""Work-package loop: plan, evidence, claims, critique, audit, manager.

Evidence defines the factual boundaries. The model may reason inside them.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .. import events
from ..db import connect
from ..distribution import route, update_package
from . import claims as claim_ledger
from . import memory, qwen_adapter
from .audit import advisory_decision, authoritative_decision, challenge_material_claims, run_supervisor_attempts
from .calc import eval_arithmetic
from .capabilities import frankenstein_status
from .completion import score_task
from .critic import critique
from .grounding import ground_text
from .planner import build_plan, classify
from .quality import score as quality_score
from .prospects import extract_prospects
from .quoting import quote_internal_doors
from .resolution import apply_manager_veto, parse_manager_decision, resolve_manager
from .skills import select_skills, skill_prompt
from .toolkit import ToolResult, invoke, now as tool_now
from .registry import route_for
from .render import render_focus, render_parent
from .research import research
from .store import (
    insert_package,
    save_model_call,
    save_observability,
    save_plan,
    save_quality,
    save_skill,
    save_source,
    save_verification,
)
from .think import strip_think
from .verifier import verify

SUPERVISOR = "research-sup"
MANAGER = "research-mgr"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_agent(agent_id: str, **fields) -> None:
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()


def _complete(role: str, system: str, user: str, max_tokens: int = 400, programme: Programme | None = None, package_id: str = ""):
    from ..models import complete_role

    try:
        text, tokens, meta = complete_role(role, system, user, max_tokens=max_tokens)
    except Exception as exc:
        return "", 0, {"error": f"{type(exc).__name__}: {exc}", "backend": "unavailable", "completion_tokens": 0, "execution": "fail"}
    if not isinstance(text, str):
        return "", 0, {"error": "malformed model response", "backend": "malformed", "completion_tokens": 0, "execution": "fail"}
    meta = dict(meta)
    meta["completion_tokens"] = tokens
    text = strip_think(text)
    if role == "EMPLOYEE" and programme is not None and qwen_adapter.runtime_mode() == "qwen-agent":
        child = next((item for item in programme.children if item["id"] == package_id), None)
        agent_id = child["agent_id"] if child else "research-e1"
        turned = qwen_adapter.employee_turn(system=system, user=user, agent_id=agent_id, package_id=package_id or programme.parent_id, preset_text=text)
        meta["runtime"] = turned.get("runtime")
        meta["tools_invoked"] = turned.get("tools") or []
        if turned.get("fallback"):
            meta["qwen_fallback"] = turned["fallback"]
        programme.runtime_notes.append({"package_id": package_id, "runtime": turned.get("runtime"), "tools": meta["tools_invoked"], "fallback": turned.get("fallback") or ""})
        text = turned.get("text") or ""
    return text, tokens, meta


class Programme:
    def __init__(self, project_id: str, objective: str, task_id: str | None = None):
        self.project_id = project_id
        self.objective = objective
        self.task_id = task_id
        self.parent_id = ""
        self.task_class = classify(objective)
        self.skills = select_skills(self.task_class, objective)
        self.plan = build_plan(objective, self.task_class, [skill.name for skill in self.skills])
        self.quote = quote_internal_doors(objective) if self.task_class == "internal_door_quote" else None
        self.research: dict = {"mode": "skipped", "queries": [], "evidence": [], "failures": [], "gaps": [], "skipped": True}
        self.children: list[dict] = []
        self.audits: list[dict] = []
        self.tokens = 0
        self.errors: list[str] = []
        self.retries = 0
        self.grounded: dict[str, str] = {}
        self.removed: list[str] = []
        self.resolution: dict = {}
        self.supervisor_tools: list[dict] = []
        self.memory_rows: list[dict] = []
        self.runtime_notes: list[dict] = []
        self._supervisor_checked = False

    def prepare_all(self) -> str:
        _set_agent(MANAGER, status="working", last_summary="Planning the work package", progress=0.2, current_tool=None)
        self.parent_id = insert_package(
            project_id=self.project_id, task_id=self.task_id, title="Research programme", objective=self.objective,
            origin="milo", agent_id=MANAGER, tier="MANAGER", model_role="MANAGER", stage="planning", status="planning",
        )
        events.emit("package.created", project_id=self.project_id, task_id=self.task_id, agent_id=MANAGER, department_id="research", status="planning", summary="Manager opened parent work package")
        events.emit("agent.planning", project_id=self.project_id, task_id=self.task_id, agent_id=MANAGER, department_id="research", status="planning", summary=f"Classified as {self.task_class}")
        plan_id = save_plan(self.parent_id, self.plan)
        for skill in self.skills:
            save_skill(self.parent_id, skill.name, skill.version, f"selected for {self.task_class}")
        update_package(
            self.parent_id,
            plan_id=plan_id,
            task_class=self.task_class,
            selected_skills=",".join(skill.name for skill in self.skills),
            selected_model=route_for(self.task_class, "planning")["model_id"],
        )
        if "research" in self.plan["stages"]:
            _set_agent("research-e3", status="researching", current_tool="web_search", last_summary="Collecting evidence", progress=0.35)
            self.research = research(self.task_class, self.objective, self.parent_id, "research-e3", project_id=self.project_id)
            for item in self.research.get("evidence") or []:
                save_source(self.parent_id, item.get("source_url") or "", item.get("source_title") or "", item.get("extracted_content") or "", "opened_page")
        self.plan["unknowns"] = list(self.research.get("gaps") or [])
        if self.quote:
            self.plan["unknowns"].extend(self.quote["missing_fields"][:6])
        for spec in self.plan["children"]:
            child_id = insert_package(
                project_id=self.project_id, task_id=self.task_id, title=spec["title"], objective=self.objective,
                origin="manager", agent_id=spec["agent_id"], parent_id=self.parent_id, tier="EMPLOYEE",
                model_role="EMPLOYEE", stage="employee", status="drafting",
            )
            events.emit("package.created", project_id=self.project_id, task_id=self.task_id, agent_id=spec["agent_id"], department_id="research", status="assigned", summary=f"Child package for {spec['agent_id']}: {spec['title']}")
            self._claims_for(child_id, spec)
            report = render_focus(spec["focus"], self._facts())
            critique_payload = critique(self.task_class, report, claim_ledger.list_claims(child_id), self.research, self.quote)
            verification = verify(self.objective, self.task_class, claim_ledger.list_claims(child_id), self.research)
            if verification["pass_rate"] < 1 and spec["focus"] == "scenarios":
                report = render_focus(spec["focus"], self._facts())
                verification = verify(self.objective, self.task_class, claim_ledger.list_claims(child_id), self.research)
            save_verification(child_id, "critic", spec["agent_id"], "", critique_payload)
            save_verification(child_id, "verifier", spec["agent_id"], "PASS" if verification["pass_rate"] == 1 else "REVISE", verification)
            update_package(child_id, findings=report, stage="supervisor", status="review", task_class=self.task_class, attempt_count=1, selected_skills=",".join(s.name for s in self.skills))
            self.children.append({**spec, "id": child_id, "report": report, "critique": critique_payload, "verification": verification})
        _set_agent("research-e3", status="idle", current_tool=None, last_summary="Evidence stored", progress=0.5)
        return self.parent_id

    def _facts(self) -> dict:
        evidence = self.research.get("evidence") or []
        prospects = extract_prospects(evidence) if self.task_class == "vending_prospects" else []
        return {
            "objective": self.objective,
            "task_class": self.task_class,
            "research": self.research,
            "quote": self.quote,
            "children": self.children,
            "prospects": prospects,
            "synthesis": "\n\n".join(part for part in self.grounded.values() if part),
            "resolution": self.resolution,
        }

    def _claims_for(self, child_id: str, spec: dict) -> None:
        focus = spec["focus"]
        agent = spec["agent_id"]
        if focus == "scenarios" and self.quote:
            claim_ledger.claims_from_quote(child_id, agent, self.quote)
        elif focus == "gaps" and self.quote:
            for field in self.quote["missing_fields"]:
                claim_ledger.add_claim(
                    child_id, agent, f"Missing before any quote: {field}", "MISSING_INFORMATION",
                    evidence_text="The objective does not state this field.", source_type="INPUT",
                    freshness="INPUT", evidence_level="deterministic", status="SUPPORTED",
                )
        elif focus in ("routes", "evidence", "prospects") and self.research.get("evidence"):
            claim_ledger.claims_from_evidence(child_id, agent, self.research["evidence"])
            if focus == "prospects":
                for prospect in extract_prospects(self.research["evidence"]):
                    claim_ledger.add_claim(
                        child_id, agent, prospect["fact"], "PROSPECT",
                        evidence_text=prospect["excerpt"], source_url=prospect["url"],
                        source_type=prospect["source_rank"], freshness=prospect["freshness"],
                        evidence_level="page", source_title=prospect["organisation"], status="SUPPORTED",
                    )
                    claim_ledger.add_claim(
                        child_id, agent,
                        f"INFERENCE: {prospect['organisation']} is worth investigating as a vending placement prospect.",
                        "INFERENCE",
                        evidence_text=prospect["excerpt"], source_url=prospect["url"],
                        source_type=prospect["source_rank"], freshness=prospect["freshness"],
                        evidence_level="page", status="PARTIALLY_SUPPORTED",
                    )
        elif focus == "prospects":
            claim_ledger.add_claim(
                child_id, agent,
                "No opened page named a vending placement prospect, so none is listed.",
                "PROSPECT",
                evidence_text=" ".join(self.research.get("gaps") or ["no prospect evidence"]),
                source_type="INPUT", freshness="INPUT", evidence_level="deterministic", status="SUPPORTED",
            )
        if self.research.get("failures") and focus in ("gaps", "evidence", "routes"):
            for failure in self.research["failures"]:
                claim_ledger.add_claim(
                    child_id, agent,
                    f"Retrieval failed at {failure.get('stage')}: {failure.get('error')}",
                    "SOURCE_FAILURE",
                    evidence_text=json.dumps(failure),
                    source_url=failure.get("url") or "",
                    source_type="UNKNOWN", freshness="UNKNOWN", evidence_level="page", status="UNVERIFIED",
                )

    def prompts(self, role: str) -> list[dict]:
        if role == "EMPLOYEE":
            return [{"id": child["id"], "system": _employee_system(self), "user": _employee_user(self, child)} for child in self.children]
        if role == "SUPERVISOR":
            return [{"id": child["id"], "system": _supervisor_system(), "user": _supervisor_user(self, child)} for child in self.children]
        if role == "MANAGER":
            return [{"id": self.parent_id, "system": _manager_system(), "user": _manager_user(self)}]
        return []

    def bind(self, role: str, package_id: str, text: str, meta: dict | None = None) -> None:
        meta = dict(meta or {})
        clean = strip_think(text or "")
        meta.setdefault("backend", meta.get("execution") or "stub")
        if role == "EMPLOYEE":
            self._bind_employee(package_id, clean, meta)
        elif role == "SUPERVISOR":
            self._bind_supervisor(package_id, clean, meta)
        elif role == "MANAGER":
            self._bind_manager(clean, meta)

    def _corpus(self) -> tuple[str, str]:
        evidence = "\n".join(
            f"{item.get('source_url') or ''} {item.get('extracted_content') or ''}"
            for item in self.research.get("evidence") or []
        )
        deterministic = ""
        if self.quote:
            door = self.quote["scenarios"]["labour_per_door"]["total_ex_vat"]
            job = self.quote["scenarios"]["labour_per_job"]["total_ex_vat"]
            deterministic = f"{door} {job} {self.quote['per_door_ex_delivery']} {self.quote['labour_unit']} {self.quote['vat']}"
        return evidence, deterministic

    def _bind_employee(self, package_id: str, text: str, meta: dict) -> None:
        child = next(item for item in self.children if item["id"] == package_id)
        if meta.get("error"):
            self.errors.append(str(meta.get("error")))
        save_model_call(package_id, "EMPLOYEE", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        evidence, deterministic = self._corpus()
        grounded = ground_text(text, evidence, deterministic)
        self.grounded[package_id] = grounded["text"]
        self.removed.extend(grounded["removed"])
        update_package(package_id, selected_model=meta.get("model") or route_for(self.task_class, "draft")["model_id"])
        _set_agent(child["agent_id"], status="idle", last_summary="Ledger draft submitted", progress=0.7, current_tool=None)

    def _bind_supervisor(self, package_id: str, text: str, meta: dict) -> None:
        child = next(item for item in self.children if item["id"] == package_id)
        save_model_call(package_id, "SUPERVISOR", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        if meta.get("error"):
            self.errors.append(str(meta.get("error")))
        evidence, _deterministic = self._corpus()
        self._supervisor_independent(package_id)
        challenges = challenge_material_claims(
            claim_ledger.list_claims(package_id), evidence, self.quote, text,
        )
        for item in challenges:
            if item["result"] == "DISPROVED" and item.get("claim_id"):
                claim_ledger.challenge(item["claim_id"], SUPERVISOR, item["resolution"], "CONTRADICTED")
        save_verification(package_id, "supervisor_challenge", SUPERVISOR, "CHALLENGED", {"challenges": challenges})
        conflicts = list(self.research.get("conflicts") or [])
        advisory = advisory_decision(text)

        def _decide(attempt: int, report=child["report"]) -> str:
            return authoritative_decision(report, self.task_class, child["focus"], attempt, conflicts=conflicts or None)

        first = _decide(1)
        if first == "RETURN":
            self.retries += 1
            child["report"] = render_focus(child["focus"], self._facts())
            outcome = run_supervisor_attempts(lambda attempt: _decide(attempt, child["report"]) if attempt > 1 else "RETURN", limit=2)
            decision = outcome["decision"]
            if decision == "ACCEPT":
                update_package(package_id, findings=child["report"], attempt_count=outcome["attempts"], return_reason="")
            else:
                decision = "TAKE_OVER" if decision == "RETURN" else decision
                update_package(package_id, findings=child["report"], review_status=decision, attempt_count=outcome["attempts"], return_reason="Retry limit reached" if outcome["limited"] else "")
        elif first == "TAKE_OVER":
            child["report"] = render_focus(child["focus"], self._facts())
            decision = authoritative_decision(child["report"], self.task_class, child["focus"], attempt=2, conflicts=conflicts or None)
            if decision != "ACCEPT":
                decision = "TAKE_OVER"
            update_package(package_id, findings=child["report"], review_status="TAKE_OVER", return_reason="Supervisor rewrote from the ledger")
        else:
            decision = first
        if advisory and advisory != decision:
            save_verification(package_id, "supervisor_disagreement", SUPERVISOR, decision, {"advisory": advisory, "authoritative": decision})
        audit = {
            "package_id": package_id,
            "focus": child["focus"],
            "decision": decision,
            "advisory": advisory,
            "issues": child["critique"].get("inconsistencies") or [],
            "unsupported_claims": [item["text"] for item in child["critique"].get("weak_claims") or []],
            "contradictions": [],
            "missing_information": child["critique"].get("unanswered") or [],
            "required_actions": ["human clarification"] if self.plan["human_approval_required"] else [],
            "quality_assessment": "authoritative checks passed" if decision == "ACCEPT" else decision,
        }
        save_verification(package_id, "supervisor", SUPERVISOR, decision, audit)
        update_package(package_id, supervisor_decision=decision, review_status=decision, stage="manager", status="accepted" if decision in ("ACCEPT", "TAKE_OVER") else "returned")
        events.emit(
            "package.accepted" if decision in ("ACCEPT", "TAKE_OVER") else "package.returned",
            project_id=self.project_id, task_id=self.task_id, agent_id=SUPERVISOR, department_id="research",
            status=decision.lower(), summary=f"Supervisor {decision} on {child['focus']}",
        )
        self.audits.append(audit)
        _set_agent(SUPERVISOR, status="working", last_summary=f"{decision} {child['focus']}", progress=0.8)

    def _supervisor_independent(self, package_id: str) -> None:
        """One independent tool check. The prompt never includes employee reasoning."""
        if self._supervisor_checked:
            return
        self._supervisor_checked = True
        if self.quote and self.quote.get("prices"):
            prices = self.quote["prices"]
            expr = "+".join(str(prices[key]) for key in ("door", "handle", "hinges", "consumables", "labour") if prices.get(key))

            def _calc(expression: str = expr) -> ToolResult:
                try:
                    value = eval_arithmetic(expression)
                except Exception as exc:
                    return ToolResult(tool="calculator", status="error", query=expression, error=str(exc), timestamp=tool_now(), metadata={"independent": True})
                return ToolResult(tool="calculator", status="ok", query=expression, extracted_content=value, timestamp=tool_now(), metadata={"independent": True, "role": "supervisor"})

            result = invoke(SUPERVISOR, "calculator", package_id, _calc)
            self.supervisor_tools.append({"tool": "calculator", "status": result.status, "output": result.extracted_content})
            return
        evidence = self.research.get("evidence") or []
        if not evidence:
            return
        item = evidence[0]
        url = item.get("source_url") or ""

        def _fetch() -> ToolResult:
            return ToolResult(
                tool="fetch_page",
                status="ok",
                query=url,
                source_url=url,
                extracted_content=(item.get("extracted_content") or "")[:500],
                timestamp=tool_now(),
                metadata={"independent": True, "role": "supervisor", "source": "re-read stored evidence"},
            )

        result = invoke(SUPERVISOR, "fetch_page", package_id, _fetch)
        self.supervisor_tools.append({"tool": "fetch_page", "status": result.status, "url": url})

    def _bind_manager(self, text: str, meta: dict) -> None:
        save_model_call(self.parent_id, "MANAGER", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        if meta.get("error"):
            self.errors.append(str(meta.get("error")))
        evidence, deterministic = self._corpus()
        grounded = ground_text(text, evidence, deterministic)
        if grounded["text"]:
            self.grounded[self.parent_id] = grounded["text"]
        self.removed.extend(grounded["removed"])
        advisory = advisory_decision(text)
        all_ids = [self.parent_id, *[child["id"] for child in self.children]]
        all_claims = claim_ledger.list_claims(project_package_ids=all_ids)
        safety = resolve_manager(
            task_class=self.task_class,
            audits=self.audits,
            claims=all_claims,
            quote=self.quote,
            research=self.research,
            advisory=advisory,
        )
        proposal, rationale = parse_manager_decision(strip_think(text))
        self.resolution = apply_manager_veto(proposal, safety)
        self.resolution["rationale"] = rationale
        self.resolution["model_judgement"] = True
        decision = self.resolution["decision"]
        facts = self._facts()
        completion = score_task(self.task_class, render_parent(facts, self.audits, decision), self.research, self.quote)
        findings = render_parent(facts, self.audits, decision, completion)
        verification = verify(self.objective, self.task_class, all_claims, self.research)
        quality = quality_score(self.task_class, all_claims, self.research, verification, "ACCEPT", self.quote)
        save_verification(self.parent_id, "manager", MANAGER, decision, {
            "decision": decision,
            "advisory": advisory,
            "audits": self.audits,
            "received_supervisor_audits": True,
            "frontier_called": False,
            "resolution": self.resolution,
            "completion": completion,
            "qwen_agent": qwen_adapter.status(),
        })
        save_quality(self.parent_id, quality)
        memory.remember("PROJECT", self.project_id, _memory_text(self, decision), provenance=f"package:{self.parent_id}", tags=self.task_class)
        if self.skills:
            memory.remember("DOMAIN", self.skills[0].name, _memory_text(self, decision), provenance=f"package:{self.parent_id}", tags=self.task_class)
        observability = {
            "plan": self.plan,
            "selected_models": {
                "employee": route_for(self.task_class, "draft"),
                "supervisor": route_for(self.task_class, "supervisor"),
                "manager": route_for(self.task_class, "manager"),
                "calculator": route_for(self.task_class, "calculation"),
                "escalation": route_for(self.task_class, "escalation"),
            },
            "skills": [skill.name for skill in self.skills],
            "queries": self.research.get("queries") or [],
            "pages": [item.get("source_url") for item in self.research.get("evidence") or []],
            "failures": self.research.get("failures") or [],
            "supervisor_decisions": [{k: audit[k] for k in ("focus", "decision", "advisory")} for audit in self.audits],
            "manager_decision": decision,
            "resolution": self.resolution,
            "completion": completion,
            "unsupported_removed": self.removed,
            "retries": self.retries,
            "tokens": self.tokens,
            "runtime": self.runtime_notes,
            "supervisor_tools": self.supervisor_tools,
            "memory_ids": [row.get("id") for row in self.memory_rows],
            "skills_loaded": [{"name": skill.name, "tools": skill.tools, "evidence": skill.evidence, "checks": skill.checks, "permissions": skill.requested_permissions} for skill in self.skills],
            "frankenstein": frankenstein_status(),
            "errors": self.errors,
            "research_mode": self.research.get("mode"),
            "frontier_called": False,
            "quality": quality,
        }
        save_observability(self.parent_id, observability)
        clarification = decision == "CLARIFY"
        if decision == "ESCALATE":
            update_package(
                self.parent_id, findings=findings, stage="escalation_required", status="escalation_required",
                manager_decision=decision, quality_score=quality["overall"], next_action="Frontier escalation is disabled",
                selected_model=route_for(self.task_class, "escalation")["model_id"],
            )
            events.emit("package.escalation_required", project_id=self.project_id, agent_id=MANAGER, department_id="research", status="escalation_required", summary="Escalation recorded. No frontier API was called.")
            _set_agent(MANAGER, status="idle", last_summary="Escalation disabled")
            return
        update_package(
            self.parent_id, findings=findings, requires_approval=1 if clarification else 0,
            manager_decision=decision, quality_score=quality["overall"], stage="distribution", status="routing",
            next_action="Lee approval" if clarification else "Present to Milo",
            selected_model=route_for(self.task_class, "manager")["model_id"],
        )
        if clarification:
            conn = connect()
            conn.execute(
                "INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), self.task_id or self.parent_id, self.project_id, MANAGER, _approval_summary(self.task_class), "pending", _now()),
            )
            conn.commit()
            conn.close()
            events.emit("approval.requested", project_id=self.project_id, task_id=self.task_id, agent_id=MANAGER, department_id="command", status="pending", summary=_approval_summary(self.task_class))
        route(self.parent_id)
        _set_agent(MANAGER, status="needs_approval" if clarification else "idle", last_summary=decision, progress=1)
        _set_agent(SUPERVISOR, status="idle", last_summary="Audit complete", progress=1)


def run_objective(project_id: str, objective: str, task_id: str | None = None) -> str:
    programme = Programme(project_id, objective, task_id)
    programme.prepare_all()
    for role in ("EMPLOYEE", "SUPERVISOR", "MANAGER"):
        for prompt in programme.prompts(role):
            text, _tokens, meta = _complete(role, prompt["system"], prompt["user"], max_tokens=320 if role != "MANAGER" else 480, programme=programme, package_id=prompt["id"])
            programme.bind(role, prompt["id"], text, meta)
    return programme.parent_id


def _employee_system(programme: Programme) -> str:
    return (
        "Evidence defines the factual boundaries. You provide reasoning and synthesis within them. "
        "You may explain, compare, prioritise, spot patterns and implications, recommend next research, "
        "explain uncertainty, and give business reasoning. "
        "Label sentences FACT, INFERENCE, RECOMMENDATION, or UNKNOWN. "
        "Do not silently add prices, URLs, companies, contacts, footfall, or availability that the evidence does not state. "
        "Do not emit think tags. Arithmetic totals come from the calculator. "
        "Memory in the user message is context, not evidence.\n\n"
        + skill_prompt(programme.skills)
    )


def _employee_user(programme: Programme, child: dict) -> str:
    programme.memory_rows = memory.retrieve(programme.objective, limit=3)
    remembered = memory.format_for_prompt(programme.memory_rows)
    block = f"\n\n{remembered}" if remembered else ""
    return (
        "Ayven tool runtime. When you need a tool, emit a line "
        'TOOL ayven_tool {"tool":"record_review","payload":"why"} and then the answer.\n'
        f"Focus: {child['focus']}\nObjective:\n{programme.objective}\n\nPublished draft:\n{child['report'][:2500]}{block}"
    )


def _supervisor_system() -> str:
    return (
        "You are the Ayven supervisor. Try to disprove material employee claims before you accept them. "
        "Check arithmetic, URLs, entailment, and unsupported specificity. "
        "First line is one of ACCEPT, RETURN, TAKE_OVER, ESCALATE. "
        "Do not repeat the employee if the evidence disagrees. Do not emit think tags."
    )


def _supervisor_user(programme: Programme, child: dict) -> str:
    claims = claim_ledger.list_claims(child["id"])
    brief = "\n".join(f"- {c['status']} {c['claim_type']}: {c['claim_text'][:180]}" for c in claims[:12])
    gaps = "; ".join((programme.research.get("gaps") or [])[:4])
    return (
        f"Objective:\n{programme.objective}\n\nPlan class: {programme.task_class}\n"
        f"Gaps: {gaps}\n\nDraft:\n{child['report'][:2000]}\n\nClaims:\n{brief}"
    )


def _manager_system() -> str:
    return (
        "You are the Ayven manager. Reason over the objective, the employee deliverable, the evidence, "
        "the claim ledger, supervisor challenges, retries, contradictions, and gaps. "
        "First line is one of SYNTHESISE, RESEARCH_MORE, RETURN, CLARIFY, ESCALATE. "
        "Then one line: Rationale: a short reason. Do not emit think tags. Do not call a frontier model. "
        "Safety and permissions can veto you."
    )


def _manager_user(programme: Programme) -> str:
    lines = [f"{audit['focus']}={audit['decision']}" for audit in programme.audits]
    gaps = "; ".join((programme.research.get("gaps") or [])[:4])
    return (
        "manager judgement\n"
        f"Objective:\n{programme.objective}\n\nSupervisor audits: {', '.join(lines) or 'pending'}\n"
        f"Unknowns: {programme.plan.get('unknowns', [])[:6]}\nGaps: {gaps}\nRetries: {programme.retries}"
    )


def _memory_text(programme: Programme, decision: str) -> str:
    gaps = "; ".join((programme.research.get("gaps") or [])[:3])
    if programme.quote:
        return f"{programme.task_class}: labour {programme.quote['labour_unit']}; VAT not applied; manager {decision}. {gaps}"
    return f"{programme.task_class}: manager {decision}. {gaps}".strip()


def _approval_summary(task_class: str) -> str:
    return {
        "internal_door_quote": "Confirm labour unit, VAT, measurements and spec before any customer quote. Nothing was sent.",
        "football_tickets": "Approve any enquiry before it is sent. No purchase was made.",
        "vending_prospects": "Approve outreach before contact. The draft was not sent.",
    }.get(task_class, "Approve the next external action. Nothing was sent.")
