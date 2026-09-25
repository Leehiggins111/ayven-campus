"""Work-package loop: plan, evidence, claims, critique, audit, manager.

Model calls comment on the ledger. They do not become the ledger.
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
from .audit import advisory_decision, authoritative_decision
from .critic import critique
from .planner import build_plan, classify
from .quality import score as quality_score
from .quoting import quote_internal_doors
from .registry import route_for
from .render import render_focus, render_parent
from .research import research
from .skills import select_skills
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


def _complete(role: str, system: str, user: str, max_tokens: int = 400):
    from ..models import complete_role

    text, tokens, meta = complete_role(role, system, user, max_tokens=max_tokens)
    meta = dict(meta)
    meta["completion_tokens"] = tokens
    return strip_think(text), tokens, meta


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
        return {"objective": self.objective, "task_class": self.task_class, "research": self.research, "quote": self.quote, "children": self.children}

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

    def _bind_employee(self, package_id: str, text: str, meta: dict) -> None:
        child = next(item for item in self.children if item["id"] == package_id)
        save_model_call(package_id, "EMPLOYEE", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        # Commentary is kept off the published findings. The ledger report stands.
        update_package(package_id, selected_model=meta.get("model") or route_for(self.task_class, "draft")["model_id"])
        _set_agent(child["agent_id"], status="idle", last_summary="Ledger draft submitted", progress=0.7, current_tool=None)

    def _bind_supervisor(self, package_id: str, text: str, meta: dict) -> None:
        child = next(item for item in self.children if item["id"] == package_id)
        save_model_call(package_id, "SUPERVISOR", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        decision = authoritative_decision(child["report"], self.task_class, child["focus"], attempt=1)
        advisory = advisory_decision(text)
        if decision == "TAKE_OVER":
            child["report"] = render_focus(child["focus"], self._facts())
            decision = authoritative_decision(child["report"], self.task_class, child["focus"], attempt=2)
            if decision != "ACCEPT":
                decision = "TAKE_OVER"
            update_package(package_id, findings=child["report"], review_status="TAKE_OVER", return_reason="Supervisor rewrote from the ledger")
        elif decision == "RETURN":
            child["report"] = render_focus(child["focus"], self._facts())
            decision = "TAKE_OVER" if authoritative_decision(child["report"], self.task_class, child["focus"], attempt=2) != "ACCEPT" else "ACCEPT"
            update_package(package_id, findings=child["report"], attempt_count=2, return_reason="")
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

    def _bind_manager(self, text: str, meta: dict) -> None:
        save_model_call(self.parent_id, "MANAGER", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        advisory = advisory_decision(text)
        clarification = self.plan["human_approval_required"]
        if any(audit["decision"] == "ESCALATE" for audit in self.audits):
            decision = "ESCALATE"
        elif clarification:
            decision = "CLARIFY"
        else:
            decision = "SYNTHESISE"
        findings = render_parent(self._facts(), self.audits, decision)
        all_ids = [self.parent_id, *[child["id"] for child in self.children]]
        all_claims = claim_ledger.list_claims(project_package_ids=all_ids)
        verification = verify(self.objective, self.task_class, all_claims, self.research)
        quality = quality_score(self.task_class, all_claims, self.research, verification, "ACCEPT", self.quote)
        save_verification(self.parent_id, "manager", MANAGER, decision, {
            "decision": decision,
            "advisory": advisory,
            "audits": self.audits,
            "received_supervisor_audits": True,
            "frontier_called": False,
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
            "retries": 0,
            "tokens": self.tokens,
            "errors": self.errors,
            "research_mode": self.research.get("mode"),
            "frontier_called": False,
            "quality": quality,
        }
        save_observability(self.parent_id, observability)
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
            text, _tokens, meta = _complete(role, prompt["system"], prompt["user"], max_tokens=320 if role != "MANAGER" else 480)
            programme.bind(role, prompt["id"], text, meta)
    return programme.parent_id


def _skill_block(programme: Programme) -> str:
    return "\n\n".join(f"Skill {skill.name} v{skill.version}\n{skill.body}" for skill in programme.skills)


def _employee_system(programme: Programme) -> str:
    return (
        "You are an Ayven employee. Comment only on the ledger. Do not add prices, URLs, companies, or availability. "
        "Do not emit think tags. Arithmetic is already done by the calculator.\n\n" + _skill_block(programme)
    )


def _employee_user(programme: Programme, child: dict) -> str:
    return f"Focus: {child['focus']}\nObjective:\n{programme.objective}\n\nPublished draft:\n{child['report'][:2500]}"


def _supervisor_system() -> str:
    return (
        "You are the Ayven supervisor. Audit independently. First line is one of ACCEPT, RETURN, TAKE_OVER, ESCALATE. "
        "Do not repeat the employee if the ledger disagrees. Do not emit think tags. Recompute before you trust a total."
    )


def _supervisor_user(programme: Programme, child: dict) -> str:
    claims = claim_ledger.list_claims(child["id"])
    brief = "\n".join(f"- {c['status']} {c['claim_type']}: {c['claim_text'][:180]}" for c in claims[:12])
    return f"Objective:\n{programme.objective}\n\nPlan class: {programme.task_class}\n\nDraft:\n{child['report'][:2000]}\n\nClaims:\n{brief}"


def _manager_system() -> str:
    return (
        "You are the Ayven manager. Decide whether evidence is sufficient, whether a person must clarify, "
        "or whether local work is exhausted. Do not call a frontier model. Do not emit think tags. "
        "First line is one of SYNTHESISE, CLARIFY, RESEARCH_MORE, ESCALATE."
    )


def _manager_user(programme: Programme) -> str:
    lines = [f"{audit['focus']}={audit['decision']}" for audit in programme.audits]
    return f"Objective:\n{programme.objective}\n\nSupervisor audits: {', '.join(lines) or 'pending'}\nUnknowns: {programme.plan.get('unknowns', [])[:6]}"


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
