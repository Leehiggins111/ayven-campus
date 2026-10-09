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
from .repair import apply_repairs, repair_rate
from .research import research
from .selfcheck import self_check
from .contracts import evaluate_contract
from .workflow import log_transition, state_for_manager
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
    from .campus_view import set_visual

    visual = fields.pop("visual_state", "")
    if visual:
        set_visual(agent_id, visual, **fields)
        return
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE agents SET {sets} WHERE id=?", [*fields.values(), agent_id])
    conn.commit()
    conn.close()


def _complete(role: str, system: str, user: str, max_tokens: int = 400, programme: Programme | None = None, package_id: str = ""):
    from ..models import complete_role

    session = getattr(programme, "employee_session", None) if programme is not None else None
    live_employee = (
        role == "EMPLOYEE"
        and programme is not None
        and qwen_adapter.runtime_mode() == "qwen-agent"
        and qwen_adapter.choose_qwen_mode(session=session, preset_text=None) == "live"
    )
    if live_employee:
        child = next((item for item in programme.children if item["id"] == package_id), None)
        agent_id = child["agent_id"] if child else "research-e1"
        turned = qwen_adapter.employee_turn(
            system=system, user=user, agent_id=agent_id, package_id=package_id or programme.parent_id,
            preset_text=None, session=session,
        )
        meta = dict(turned.get("meta") or {})
        meta.update({
            "backend": "qwen-agent",
            "execution": "real",
            "runtime": turned.get("runtime"),
            "qwen_mode": turned.get("qwen_mode") or "live",
            "tools_invoked": turned.get("tools") or [],
            "completion_tokens": int(meta.get("completion_tokens") or 0),
        })
        if turned.get("fallback"):
            meta["qwen_fallback"] = turned["fallback"]
        programme.runtime_notes.append({
            "package_id": package_id,
            "runtime": turned.get("runtime"),
            "qwen_mode": meta["qwen_mode"],
            "tools": meta["tools_invoked"],
            "fallback": turned.get("fallback") or "",
        })
        return turned.get("text") or "", meta["completion_tokens"], meta
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
        turned = qwen_adapter.employee_turn(
            system=system, user=user, agent_id=agent_id, package_id=package_id or programme.parent_id, preset_text=text,
        )
        meta["runtime"] = turned.get("runtime")
        meta["qwen_mode"] = turned.get("qwen_mode") or "replay"
        meta["tools_invoked"] = turned.get("tools") or []
        if turned.get("fallback"):
            meta["qwen_fallback"] = turned["fallback"]
        programme.runtime_notes.append({
            "package_id": package_id,
            "runtime": turned.get("runtime"),
            "qwen_mode": meta["qwen_mode"],
            "tools": meta["tools_invoked"],
            "fallback": turned.get("fallback") or "",
        })
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
        self.removed_by: dict[str, list[str]] = {}
        self.resolution: dict = {}
        self.supervisor_tools: list[dict] = []
        self.memory_rows: list[dict] = []
        self.runtime_notes: list[dict] = []
        self._supervisor_checked: set[str] = set()
        self.employee_session = None
        self.repairs: list[dict] = []
        self.self_checks: list[dict] = []
        self.material_challenges: list[dict] = []
        self.document: dict | None = None
        self.calculation: dict | None = None
        self.paused = ""
        self.resume_id = ""
        self.clarification_answer = ""
        from .observability import new_trace_id

        self.trace_id = new_trace_id()

    def prepare_all(self) -> str:
        from .observability import bind_trace, reset_trace

        token = bind_trace(self.trace_id)
        try:
            return self._prepare_all()
        finally:
            reset_trace(token)

    def _prepare_all(self) -> str:
        from .campus_view import mark_stage
        from .clarification import blocking_question

        _set_agent(MANAGER, visual_state="PLANNING", status="working", last_summary="Planning the work package", progress=0.2, current_tool=None)
        if self.resume_id:
            self.parent_id = self.resume_id
            mark_stage(self.parent_id, "PLANNING", project_id=self.project_id, task_id=self.task_id or "")
        else:
            self.parent_id = insert_package(
                project_id=self.project_id, task_id=self.task_id, title="Research programme", objective=self.objective,
                origin="milo", agent_id=MANAGER, tier="MANAGER", model_role="MANAGER", stage="planning", status="planning",
            )
            events.emit("package.created", project_id=self.project_id, task_id=self.task_id, agent_id=MANAGER, department_id="research", status="planning", summary="Manager opened parent work package")
            log_transition(self.parent_id, "IN_PROGRESS", "work package opened")
            mark_stage(self.parent_id, "REQUEST", project_id=self.project_id, task_id=self.task_id or "")
            mark_stage(self.parent_id, "PLANNING", project_id=self.project_id, task_id=self.task_id or "")
        events.emit("agent.planning", project_id=self.project_id, task_id=self.task_id, agent_id=MANAGER, department_id="research", status="PLANNING", summary=f"Classified as {self.task_class}")
        question = blocking_question(self.objective) if not self.clarification_answer else ""
        if self.task_class == "calculation":
            self.calculation = _calculation_from_objective(self.clarification_answer or self.objective)
            if not self.calculation and not question:
                question = "Which numbers and operation should I calculate? I could not reliably interpret that expression."
        if question:
            self.paused = "clarification"
            update_package(
                self.parent_id,
                clarification_question=question,
                status="needs_clarification",
                next_action="Lee must answer before this package continues",
                campus_stage="CLARIFICATION",
            )
            log_transition(self.parent_id, "AWAITING_CLARIFICATION", question)
            _set_agent(MANAGER, visual_state="WAITING", status="waiting", last_summary=question, progress=0.3)
            _set_agent("milo", visual_state="WAITING", status="waiting", last_summary=question, progress=0.3)
            events.emit("package.needs_clarification", project_id=self.project_id, task_id=self.task_id, agent_id=MANAGER, department_id="research", status="AWAITING_CLARIFICATION", summary=question)
            return self.parent_id
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
        if self.task_class == "calculation":
            self.research["calculation"] = self.calculation or {}
        if "research" in self.plan["stages"]:
            from .recovery import run_bounded

            mark_stage(self.parent_id, "RESEARCH", project_id=self.project_id, task_id=self.task_id or "")
            _set_agent("research-e3", visual_state="RESEARCHING", status="researching", current_tool="web_search", last_summary="Collecting evidence", progress=0.35)
            _set_agent("research-e3", visual_state="USING_TOOL", status="using_tool", current_tool="web_search", last_summary="Opening sources", progress=0.4)
            researched, err = run_bounded(
                "research",
                lambda: research(self.task_class, self.objective, self.parent_id, "research-e3", project_id=self.project_id, skills=self.skills),
            )
            if err:
                self.errors.append(err)
                self.research = {
                    "mode": "timeout",
                    "queries": [],
                    "evidence": [],
                    "failures": [{"stage": "research", "error": err}],
                    "gaps": [f"Gap: {err}"],
                    "skipped": False,
                }
            else:
                self.research = researched or self.research
            attached = _attached_document(self.objective)
            self.document = attached
            if attached:
                self.research.setdefault("evidence", []).append(attached)
                meta = attached.get("metadata") or {}
                claim_ledger.add_claim(
                    self.parent_id,
                    "research-e3",
                    (attached.get("extracted_content") or "")[:500],
                    "DOCUMENT",
                    evidence_text=(attached.get("extracted_content") or "")[:500],
                    source_url=attached.get("source_url") or "",
                    source_type="PRIMARY_DOCUMENT",
                    freshness="INPUT",
                    evidence_level="page",
                    source_title=attached.get("source_title") or "",
                    status="SUPPORTED",
                    locator=str(meta.get("locator") or ""),
                    file_hash=str(meta.get("sha256") or ""),
                    origin="document",
                )
            for item in self.research.get("evidence") or []:
                save_source(self.parent_id, item.get("source_url") or "", item.get("source_title") or "", item.get("extracted_content") or "", "opened_page")
            mark_stage(self.parent_id, "EVIDENCE", project_id=self.project_id, task_id=self.task_id or "")
        self.plan["unknowns"] = list(self.research.get("gaps") or [])
        if self.quote:
            self.plan["unknowns"].extend(self.quote["missing_fields"][:6])
        mark_stage(self.parent_id, "EMPLOYEE", project_id=self.project_id, task_id=self.task_id or "")
        for spec in self.plan["children"]:
            _set_agent(spec["agent_id"], visual_state="WRITING", status="working", last_summary=f"Drafting {spec['focus']}", progress=0.55)
            child_id = insert_package(
                project_id=self.project_id, task_id=self.task_id, title=spec["title"], objective=self.objective,
                origin="manager", agent_id=spec["agent_id"], parent_id=self.parent_id, tier="EMPLOYEE",
                model_role="EMPLOYEE", stage="employee", status="drafting",
            )
            events.emit("package.created", project_id=self.project_id, task_id=self.task_id, agent_id=spec["agent_id"], department_id="research", status="assigned", summary=f"Child package for {spec['agent_id']}: {spec['title']}")
            log_transition(child_id, "IN_PROGRESS", f"assigned {spec['focus']}")
            self._claims_for(child_id, spec)
            report = render_focus(spec["focus"], self._facts())
            if self.document and spec["focus"] in ("evidence", "gaps", "draft"):
                report = (self.document.get("extracted_content") or "")[:1500] + "\n" + report
            critique_payload = critique(self.task_class, report, claim_ledger.list_claims(child_id), self.research, self.quote)
            verification = verify(self.objective, self.task_class, claim_ledger.list_claims(child_id), self.research)
            if verification["pass_rate"] < 1 and spec["focus"] == "scenarios":
                report = render_focus(spec["focus"], self._facts())
                verification = verify(self.objective, self.task_class, claim_ledger.list_claims(child_id), self.research)
            save_verification(child_id, "critic", spec["agent_id"], "", critique_payload)
            save_verification(child_id, "verifier", spec["agent_id"], "PASS" if verification["pass_rate"] == 1 else "REVISE", verification)
            update_package(child_id, findings=report, stage="supervisor", status="review", task_class=self.task_class, attempt_count=1, selected_skills=",".join(s.name for s in self.skills))
            self.children.append({**spec, "id": child_id, "report": report, "critique": critique_payload, "verification": verification})
        mark_stage(self.parent_id, "DRAFT", project_id=self.project_id, task_id=self.task_id or "")
        _set_agent("research-e3", visual_state="IDLE", status="idle", current_tool=None, last_summary="Evidence stored", progress=0.5)
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
            "calculation": self.calculation,
        }

    def _claims_for(self, child_id: str, spec: dict) -> None:
        focus = spec["focus"]
        agent = spec["agent_id"]
        if focus == "scenarios" and self.calculation:
            claim_ledger.add_claim(
                child_id, agent,
                f"Calculator result {self.calculation['value']} from {self.calculation['expression']}.",
                "CALCULATION",
                evidence_text=f"{self.calculation['expression']} = {self.calculation['value']}",
                source_type="DETERMINISTIC",
                freshness="INPUT",
                evidence_level="deterministic",
                status="SUPPORTED",
                origin="calculator",
            )
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
        from .observability import bind_trace, reset_trace

        token = bind_trace(self.trace_id)
        try:
            self._bind(role, package_id, text, meta)
        finally:
            reset_trace(token)

    def _bind(self, role: str, package_id: str, text: str, meta: dict | None = None) -> None:
        meta = dict(meta or {})
        clean = strip_think(text or "")
        meta.setdefault("backend", meta.get("execution") or "stub")
        meta["trace_id"] = self.trace_id
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
        deterministic = self.objective or ""
        if self.quote:
            door = self.quote["scenarios"]["labour_per_door"]["total_ex_vat"]
            job = self.quote["scenarios"]["labour_per_job"]["total_ex_vat"]
            prices = " ".join(str(value) for value in (self.quote.get("prices") or {}).values())
            deterministic = f"{door} {job} {self.quote['per_door_ex_delivery']} {self.quote['labour_unit']} {self.quote['vat']} {prices} {self.objective}"
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
        self.removed_by.setdefault(package_id, []).extend(grounded["removed"])
        update_package(package_id, selected_model=meta.get("model") or route_for(self.task_class, "draft")["model_id"])
        _set_agent(child["agent_id"], visual_state="COMPLETED", status="idle", last_summary="Ledger draft submitted", progress=0.7, current_tool=None)

    def _bind_supervisor(self, package_id: str, text: str, meta: dict) -> None:
        from .campus_view import mark_stage

        mark_stage(self.parent_id, "SUPERVISOR", project_id=self.project_id, task_id=self.task_id or "")
        _set_agent(SUPERVISOR, visual_state="REVIEWING", status="working", last_summary="Reviewing the draft", progress=0.75)
        child = next(item for item in self.children if item["id"] == package_id)
        save_model_call(package_id, "SUPERVISOR", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        if meta.get("error"):
            self.errors.append(str(meta.get("error")))
        evidence, _deterministic = self._corpus()
        self._supervisor_independent(package_id)
        checklist = self_check(
            objective=self.objective,
            report=child["report"],
            claims=claim_ledger.list_claims(package_id),
            research=self.research,
            quote=self.quote,
        )
        self.self_checks.append({"package_id": package_id, **checklist})
        save_verification(package_id, "employee_self_check", child["agent_id"], "CHECKED", checklist)
        log_transition(package_id, "UNDER_REVIEW", "employee self-check stored; supervisor has no chain of thought")
        challenges = challenge_material_claims(
            claim_ledger.list_claims(package_id), evidence, self.quote, text, objective=self.objective,
        )
        from .audit import _entity_follow_up

        follow = _entity_follow_up(self.objective)
        for sentence in self.removed_by.get(package_id, []):
            if not follow:
                continue
            challenges.append({
                "claim_id": "",
                "challenge": "replace the unsupported published sentence",
                "result": "DISPROVED",
                "evidence": sentence[:400],
                "resolution": "Published specifics were absent from the opened pages.",
                "follow_up": follow,
            })
        self.material_challenges.extend(challenges)
        repair = apply_repairs(
            package_id,
            challenges,
            objective=self.objective,
            agent_id=child["agent_id"],
            section=child["report"],
        )
        self.repairs.extend(repair["actions"])
        challenge_by_claim = {item.get("claim_id"): item for item in challenges if item.get("claim_id")}
        for action in repair["actions"]:
            source = challenge_by_claim.get(action.get("claim_id")) or {}
            action["problem"] = (source.get("resolution") or source.get("challenge") or action.get("detail") or "")[:300]
        self.retries += int(repair["attempted"] or 0)
        if repair.get("evidence"):
            self.research.setdefault("evidence", []).extend(repair["evidence"])
        if repair["actions"]:
            from .campus_view import mark_stage

            mark_stage(self.parent_id, "REPAIR", project_id=self.project_id, task_id=self.task_id or "")
            _set_agent(SUPERVISOR, visual_state="REPAIRING", status="working", last_summary=f"Repairing {len(repair['actions'])} supervisor findings", progress=0.78)
            log_transition(package_id, "REPAIRING", "rejected material claims repaired locally")
            child["report"] = repair.get("section") or render_focus(child["focus"], self._facts())
            save_verification(package_id, "supervisor_recheck", SUPERVISOR, "RECHECKED", {"repairs": repair["actions"]})
            log_transition(package_id, "UNDER_REVIEW", "repaired section returned to the supervisor")
        settled = {
            item["claim_id"]
            for item in repair["actions"]
            if item.get("claim_id") and item["action"] in ("REMOVE_CLAIM", "REPLACE_SOURCE", "RECALCULATE", "DOWNGRADE_TO_INFERENCE", "RESEARCH_MORE", "UNRESOLVED_GAP")
        }
        for item in challenges:
            if item["result"] == "DISPROVED" and item.get("claim_id") and item["claim_id"] not in settled:
                claim_ledger.challenge(item["claim_id"], SUPERVISOR, item["resolution"], "CONTRADICTED")
        save_verification(package_id, "supervisor_challenge", SUPERVISOR, "CHALLENGED", {"challenges": challenges, "repairs": repair})
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
        _set_agent(SUPERVISOR, visual_state="REVIEWING", status="working", last_summary=f"{decision} {child['focus']}", progress=0.8)

    def _supervisor_independent(self, package_id: str) -> None:
        """Calculator for a quote, then a bounded search the supervisor writes from the claim."""
        if package_id in self._supervisor_checked:
            return
        self._supervisor_checked.add(package_id)
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
            self.supervisor_tools.append({"tool": "calculator", "status": result.status, "output": result.extracted_content, "verification": "independently_confirmed" if result.status == "ok" else "independently_unconfirmed"})
        from .research import fetch_provider, research_mode, search_provider
        from .supervisor_check import independent_verify

        claims = claim_ledger.list_claims(package_id)
        browser_allowed = research_mode() == "live"

        def _browse(url: str):
            from .browser_adapter import available, open_page

            if not available():
                return {"text": "", "error": "browser_unavailable"}
            opened = open_page(SUPERVISOR, package_id, url)
            return {"text": opened.extracted_content or "", "error": opened.error or ""}

        report = independent_verify(
            claims,
            search_fn=search_provider,
            fetch_fn=fetch_provider,
            browse_fn=_browse if browser_allowed else None,
            browser_allowed=browser_allowed,
        )
        self.supervisor_tools.append({"tool": "independent_verification", **report})
        for row in report["checks"]:
            if row["verification"] == "independently_contradicted" and row.get("claim_id"):
                claim_ledger.challenge(row["claim_id"], SUPERVISOR, "Independent page contradicted the claim.", "CONTRADICTED")

    def _bind_manager(self, text: str, meta: dict) -> None:
        from .campus_view import mark_stage

        mark_stage(self.parent_id, "MANAGER", project_id=self.project_id, task_id=self.task_id or "")
        _set_agent(MANAGER, visual_state="REVIEWING", status="working", last_summary="Manager is deciding", progress=0.9)
        save_model_call(self.parent_id, "MANAGER", self.task_class, meta, text)
        self.tokens += int(meta.get("completion_tokens") or 0)
        if meta.get("error"):
            self.errors.append(str(meta.get("error")))
        evidence, deterministic = self._corpus()
        grounded = ground_text(text, evidence, deterministic)
        if grounded["text"]:
            self.grounded[self.parent_id] = grounded["text"]
        self.removed.extend(grounded["removed"])
        log_transition(self.parent_id, "UNDER_REVIEW", "manager is resolving the package")
        log_transition(self.parent_id, "READY", "supervisor audits are in")
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
        verified_calculation = self.task_class == "calculation" and self.calculation and safety["decision"] == "SYNTHESISE"
        if verified_calculation:
            # Publishing a checked arithmetic result needs no external-action approval.
            # Preserve the model proposal for audit without blocking the calculator.
            self.resolution = {**safety, "safety_decision": safety["decision"],
                "model_proposal": proposal, "decision_source": "deterministic-calculation",
                "veto": proposal != "SYNTHESISE",
                "reason": "The restricted calculator and safety checks settled the arithmetic. No external action is requested."}
        self.resolution["rationale"] = rationale
        self.resolution["model_judgement"] = not bool(verified_calculation)
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
            "relevant_opened": sum(1 for item in self.research.get("evidence") or [] if (item.get("metadata") or {}).get("relevant")),
            "filtered_results": self.research.get("filtered") or [],
            "targets": self.research.get("targets") or [],
            "failures": self.research.get("failures") or [],
            "supervisor_decisions": [{k: audit[k] for k in ("focus", "decision", "advisory")} for audit in self.audits],
            "manager_decision": decision,
            "resolution": self.resolution,
            "completion": completion,
            "unsupported_removed": self.removed,
            "retries": self.retries,
            "repairs": self.repairs,
            "repair_rate": repair_rate(self.repairs),
            "material_challenges": self.material_challenges,
            "self_checks": self.self_checks,
            "completion_contract": self.plan.get("completion_contract"),
            "contract_evaluation": evaluate_contract(self.plan.get("completion_contract") or {}, report=findings, research=self.research, claims=all_claims),
            "tokens": self.tokens,
            "runtime": self.runtime_notes,
            "supervisor_tools": self.supervisor_tools,
            "memory_ids": [row.get("id") for row in self.memory_rows],
            "skills_loaded": [{"name": skill.name, "tools": skill.tools, "evidence": skill.evidence, "checks": skill.checks, "permissions": skill.requested_permissions} for skill in self.skills],
            "frankenstein": frankenstein_status(),
            "errors": self.errors,
            "trace_id": self.trace_id,
            "calculation": self.calculation or {},
            "research_mode": self.research.get("mode"),
            "frontier_called": False,
            "quality": quality,
        }
        from .recovery import final_evaluation

        observability["evaluation"] = final_evaluation(observability, decision=decision, findings=findings)
        save_observability(self.parent_id, observability)
        from .observability import record_trace

        record_trace(self.parent_id, "work_package", {
            "manager_decision": decision,
            "retries": self.retries,
            "repair_rate": observability.get("repair_rate"),
            "tokens": self.tokens,
            "task_class": self.task_class,
            "trace_id": self.trace_id,
            "verdict": observability["evaluation"]["verdict"],
        })
        for action in self.repairs:
            record_trace(self.parent_id, "repair", action)
        approval_required = decision in ("CLARIFY", "APPROVAL_REQUIRED") or (
            bool(self.plan.get("human_approval_required")) and decision in ("ESCALATE", "RETURN", "RESEARCH_MORE", "CLARIFY")
        )
        final_state = state_for_manager(decision, approval_required=approval_required and decision in ("CLARIFY", "APPROVAL_REQUIRED", "RETURN", "RESEARCH_MORE"))
        if decision == "ESCALATE":
            log_transition(self.parent_id, "ESCALATED", self.resolution.get("reason") or "escalated")
            if approval_required:
                _create_approval(self, "Escalation does not authorise an external action. " + _approval_summary(self.task_class))
                log_transition(self.parent_id, "AWAITING_APPROVAL", "approval record created during escalation")
            update_package(
                self.parent_id, findings=findings, stage="escalation_required", status="escalation_required",
                manager_decision=decision, quality_score=quality["overall"], next_action="Frontier escalation is disabled",
                selected_model=route_for(self.task_class, "escalation")["model_id"],
                requires_approval=1 if approval_required else 0,
            )
            events.emit("package.escalation_required", project_id=self.project_id, agent_id=MANAGER, department_id="research", status="escalation_required", summary="Escalation recorded. No frontier API was called.")
            if approval_required:
                from .campus_view import mark_stage

                mark_stage(self.parent_id, "APPROVAL", project_id=self.project_id, task_id=self.task_id or "")
                _set_agent(MANAGER, visual_state="NEEDS_APPROVAL", status="needs_approval", last_summary="Escalation needs Lee", progress=1)
            else:
                _set_agent(MANAGER, visual_state="WAITING", status="idle", last_summary="Escalation disabled", progress=1)
            return
        log_transition(self.parent_id, final_state, decision)
        clarification = bool(approval_required)
        update_package(
            self.parent_id, findings=findings, requires_approval=1 if clarification else 0,
            manager_decision=decision, quality_score=quality["overall"], stage="distribution", status="routing",
            next_action="Lee approval" if clarification else "Present to Milo",
            selected_model=route_for(self.task_class, "manager")["model_id"],
        )
        if clarification:
            _create_approval(self, _approval_summary(self.task_class), _approval_context(self, decision))
        route(self.parent_id)
        from .campus_view import mark_stage

        if clarification:
            mark_stage(self.parent_id, "APPROVAL", project_id=self.project_id, task_id=self.task_id or "")
            _set_agent(MANAGER, visual_state="NEEDS_APPROVAL", status="needs_approval", last_summary=decision, progress=1)
        elif final_state == "COMPLETED":
            mark_stage(self.parent_id, "COMPLETE", project_id=self.project_id, task_id=self.task_id or "")
            _set_agent(MANAGER, visual_state="COMPLETED", status="idle", last_summary=decision, progress=1)
            _set_agent("milo", visual_state="COMPLETED", status="idle", last_summary="Package complete", progress=1)
        else:
            mark_stage(self.parent_id, "MANAGER", project_id=self.project_id, task_id=self.task_id or "")
            _set_agent(MANAGER, visual_state="IDLE", status="idle", last_summary=decision, progress=1)
        _set_agent(SUPERVISOR, visual_state="COMPLETED", status="idle", last_summary="Audit complete", progress=1)


def _calculation_from_objective(objective: str) -> dict | None:
    from .calc import CalcError, expression_from_objective

    expression = expression_from_objective(objective)
    if not expression:
        return None
    try:
        value = eval_arithmetic(expression)
    except (CalcError, ArithmeticError):
        return None
    return {"expression": expression, "value": value}


def _attached_document(objective: str) -> dict | None:
    """A test or caller can point AYVEN_DOCUMENT_PATH at a local file. Web text cannot set it."""
    import os

    path = os.environ.get("AYVEN_DOCUMENT_PATH", "").strip()
    if not path:
        return None
    if not any(word in (objective or "").lower() for word in ("document", "pdf", "spreadsheet", "sheet", "docx")):
        return None
    from .documents import extract
    from .security import injection_signals

    doc = extract(path=path)
    if not doc.get("ok"):
        return None
    raw = doc.get("text") or ""
    signals = injection_signals(raw)
    published = raw
    if signals:
        kept = [line for line in raw.splitlines() if not injection_signals(line)]
        published = "\n".join(kept).strip() or "The document contained an untrusted instruction and no remaining fact."
    locator = str(doc.get("locator") or "")
    digest = str(doc.get("sha256") or "")
    return {
        "source_url": "file://" + path,
        "source_title": doc.get("source") or path,
        "extracted_content": f"file_hash={digest} locator={locator}\n{published}"[:4000],
        "metadata": {
            "evidence_level": "page",
            "relevant": True,
            "freshness": "INPUT",
            "source_rank": "PRIMARY_DOCUMENT",
            "page": doc.get("page"),
            "sheet": doc.get("sheet"),
            "locator": locator,
            "sha256": digest,
            "file_hash": digest,
            "injection_signals": signals,
            "action_from_document": False,
        },
    }


def run_objective(project_id: str, objective: str, task_id: str | None = None) -> str:
    import os

    from .observability import record_trace
    from .recovery import resume_parent

    if os.environ.get("AYVEN_RESUME", "0") == "1":
        existing = resume_parent(project_id)
        if existing:
            record_trace(existing, "resume", {"project_id": project_id})
            return existing
    programme = Programme(project_id, objective, task_id)
    programme.prepare_all()
    if programme.paused:
        return programme.parent_id
    _finish_roles(programme)
    return programme.parent_id


def _finish_roles(programme: Programme) -> None:
    for role in ("EMPLOYEE", "SUPERVISOR", "MANAGER"):
        for prompt in programme.prompts(role):
            text, _tokens, meta = _complete(role, prompt["system"], prompt["user"], max_tokens=320 if role != "MANAGER" else 480, programme=programme, package_id=prompt["id"])
            programme.bind(role, prompt["id"], text, meta)


def answer_clarification(package_id: str, answer: str) -> str:
    """Store Lee's answer and continue the same parent package."""
    from .store import package
    from .think import strip_think

    row = package(package_id)
    if not row:
        raise KeyError("package not found")
    if (row.get("workflow_state") or "") != "AWAITING_CLARIFICATION":
        raise ValueError("package is not waiting for clarification")
    cleaned = strip_think(answer or "").strip()
    if not cleaned:
        raise ValueError("answer is empty")
    update_package(package_id, clarification_answer=cleaned)
    log_transition(package_id, "IN_PROGRESS", "Lee answered; the same package continues")
    programme = Programme(row["project_id"], row.get("objective") or "", row.get("task_id"))
    programme.resume_id = package_id
    programme.parent_id = package_id
    programme.clarification_answer = cleaned
    programme.objective = (row.get("objective") or "").rstrip() + "\nLee answered: " + cleaned
    programme.task_class = classify(programme.objective)
    programme.skills = select_skills(programme.task_class, programme.objective)
    programme.plan = build_plan(programme.objective, programme.task_class, [skill.name for skill in programme.skills])
    programme.quote = quote_internal_doors(programme.objective) if programme.task_class == "internal_door_quote" else None
    programme.prepare_all()
    if programme.paused:
        return package_id
    _finish_roles(programme)
    return package_id


def resume_approved_package(package_id: str) -> str:
    """After the approval row is recorded, the same package completes. Nothing is sent."""
    from .campus_view import mark_stage
    from .store import package

    row = package(package_id) or {}
    if (row.get("workflow_state") or "") != "APPROVED":
        return row.get("workflow_state") or ""
    log_transition(package_id, "ACTIONING", "Lee approved; the same package continues. Nothing is sent.")
    log_transition(package_id, "COMPLETED", "Approved package completed. Nothing was sent.")
    update_package(
        package_id,
        stage="results",
        status="complete",
        destination="command",
        next_action="Complete",
    )
    mark_stage(package_id, "COMPLETE", project_id=row.get("project_id") or "", task_id=row.get("task_id") or "")
    _set_agent(MANAGER, visual_state="COMPLETED", status="idle", last_summary="Approved work is complete. Nothing was sent.", progress=1)
    _set_agent("milo", visual_state="COMPLETED", status="idle", last_summary="Package complete after approval", progress=1)
    events.emit(
        "package.completed",
        project_id=row.get("project_id"),
        task_id=row.get("task_id"),
        agent_id=MANAGER,
        department_id="research",
        status="COMPLETED",
        summary="Approved work package completed. Nothing was sent.",
    )
    if row.get("project_id"):
        conn = connect()
        conn.execute(
            "UPDATE projects SET status=?, result=? WHERE id=?",
            ("complete", row.get("findings") or "Approved. Nothing was sent.", row["project_id"]),
        )
        conn.commit()
        conn.close()
    return "COMPLETED"


def resume_packages_for_approval(approval_id: str) -> list[str]:
    conn = connect()
    approval = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    if not approval:
        conn.close()
        return []
    rows = conn.execute(
        "SELECT id FROM work_packages WHERE workflow_state='APPROVED' AND parent_id IS NULL AND (task_id=? OR project_id=?)",
        (approval["task_id"], approval["project_id"]),
    ).fetchall()
    conn.close()
    landed = []
    for row in rows:
        state = resume_approved_package(row["id"])
        if state:
            landed.append(state)
    return landed


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


def _approval_context(programme: Programme, decision: str) -> dict:
    from .think import strip_think

    evidence = []
    for item in (programme.research.get("evidence") or [])[:3]:
        evidence.append({
            "source": (item.get("source_url") or item.get("source_title") or "opened page")[:180],
            "note": (item.get("source_title") or "")[:160],
        })
    reason = strip_think((programme.resolution.get("reason") or programme.resolution.get("rationale") or "") )
    return {
        "what": _approval_summary(programme.task_class),
        "why": (reason or "A person must approve the next external action.")[:400],
        "if_approved": "The same work package continues to completion. Nothing is sent externally.",
        "if_rejected": "The work package ends rejected. Nothing is sent.",
        "evidence": evidence,
        "decision": decision,
    }


def _create_approval(programme: Programme, summary: str, context: dict | None = None) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at,context_json) VALUES(?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            programme.task_id or programme.parent_id,
            programme.project_id,
            MANAGER,
            summary,
            "pending",
            _now(),
            json.dumps(context or _approval_context(programme, ""), default=str),
        ),
    )
    conn.commit()
    conn.close()
    events.emit("approval.requested", project_id=programme.project_id, task_id=programme.task_id, agent_id=MANAGER, department_id="command", status="pending", summary=summary)


def _approval_summary(task_class: str) -> str:
    return {
        "internal_door_quote": "Confirm labour unit, VAT, measurements and spec before any customer quote. Nothing was sent.",
        "football_tickets": "Approve any enquiry before it is sent. No purchase was made.",
        "vending_prospects": "Approve outreach before contact. The draft was not sent.",
    }.get(task_class, "Approve the next external action. Nothing was sent.")
