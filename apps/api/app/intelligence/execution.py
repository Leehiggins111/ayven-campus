"""Work-package loop: plan, evidence, claims, critique, audit, manager.

Evidence defines the factual boundaries. The model may reason inside them.
"""

from __future__ import annotations

import json
import re
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
from .deliverable import clip_at_boundary
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


def _complete(role: str, system: str, user: str, max_tokens: int = 400, programme: Programme | None = None, package_id: str = "", plain: bool = False, prefill: str = "", stop=None, expand_unfinished: bool = True, ollama_format=None, plan: bool = False):
    from ..models import complete_role, plan_model

    override = plan_model() if plan else ""
    if plain or override:
        try:
            text, tokens, meta = complete_role(
                role, system, user, max_tokens=max_tokens, prefill=prefill, stop=stop,
                expand_unfinished=expand_unfinished, ollama_format=ollama_format,
                model=override or None,
            )
        except Exception as exc:
            trace = getattr(exc, "trace", None) or {}
            meta = {
                "error": f"{type(exc).__name__}: {exc}",
                "backend": "unavailable",
                "completion_tokens": int(trace.get("completion_tokens") or 0),
                "prompt_tokens": int(trace.get("prompt_tokens") or 0),
                "elapsed_s": trace.get("elapsed_s") or 0,
                "finish_reason": trace.get("finish_reason") or "",
                "raw_response": trace.get("raw_response") or "",
                "execution": "fail",
            }
            return "", 0, meta
        if not isinstance(text, str):
            return "", 0, {"error": "malformed model response", "backend": "malformed", "completion_tokens": 0, "execution": "fail"}
        meta = dict(meta)
        meta["completion_tokens"] = tokens
        return strip_think(text), tokens, meta
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
            preset_text=None, session=session, max_tokens=max_tokens,
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
        from ..models import log_generation

        reply = turned.get("text") or ""
        meta["raw_response"] = reply
        log_generation(role, meta, reply)
        return reply, meta["completion_tokens"], meta
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


def _research_kept_after_timeout(holder: dict, err: str) -> dict:
    """A stage timeout must not throw away pages that were already opened."""
    evidence = [item for item in (holder.get("evidence") or []) if isinstance(item, dict)]
    queries = list(holder.get("queries") or [])
    if not evidence and not queries:
        return {
            "mode": "timeout",
            "queries": [],
            "evidence": [],
            "failures": [{"stage": "research", "error": err}],
            "gaps": [f"Gap: {err}"],
            "skipped": False,
        }
    gaps = list(holder.get("gaps") or [])
    gaps.append("Research stopped at the time limit. Pages already opened were kept.")
    failures = list(holder.get("failures") or [])
    failures.append({"stage": "research", "error": err})
    return {
        "mode": holder.get("mode") or "timeout",
        "queries": queries,
        "query_source": holder.get("query_source") or "",
        "evidence": evidence,
        "failures": failures,
        "duplicates": list(holder.get("duplicates") or []),
        "gaps": gaps,
        "filtered": list(holder.get("filtered") or []),
        "skipped": False,
        "memory_fallback_used": False,
    }


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
            self.calculation = _calculation_from_objective(self.objective)
            self.research["calculation"] = self.calculation or {}
        if "research" in self.plan["stages"]:
            from .recovery import run_bounded

            mark_stage(self.parent_id, "RESEARCH", project_id=self.project_id, task_id=self.task_id or "")
            _set_agent("research-e3", visual_state="RESEARCHING", status="researching", current_tool="web_search", last_summary="Collecting evidence", progress=0.35)
            _set_agent("research-e3", visual_state="USING_TOOL", status="using_tool", current_tool="web_search", last_summary="Opening sources", progress=0.4)
            holder: dict = {}
            researched, err = run_bounded(
                "research",
                lambda: research(
                    self.task_class,
                    self.objective,
                    self.parent_id,
                    "research-e3",
                    project_id=self.project_id,
                    skills=self.skills,
                    sink=holder,
                ),
            )
            if err:
                self.errors.append(err)
                self.research = _research_kept_after_timeout(holder, err)
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
                    clip_at_boundary(attached.get("extracted_content") or "", 2000),
                    "DOCUMENT",
                    evidence_text=clip_at_boundary(attached.get("extracted_content") or "", 2000),
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
                report = clip_at_boundary(self.document.get("extracted_content") or "", 1500) + "\n" + report
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
            prompts = []
            for child in self.children:
                if self.task_class == "business_research" and child.get("focus") == "draft":
                    limit = 2048
                elif self.task_class == "business_research":
                    limit = 160
                else:
                    limit = 320
                prompts.append({"id": child["id"], "system": _employee_system(self), "user": _employee_user(self, child), "max_tokens": limit})
            return prompts
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
        if self.task_class == "software_build" and child.get("focus") == "build":
            report = text if "nothing was sent" in (text or "").lower() else (text or "").rstrip() + "\nNothing was sent.\n"
            child["report"] = report
            update_package(package_id, findings=report, selected_model=meta.get("model") or route_for(self.task_class, "coding")["model_id"])
            _set_agent(child["agent_id"], visual_state="COMPLETED", status="idle", last_summary="Program submitted", progress=0.7, current_tool=None)
            return
        evidence, deterministic = self._corpus()
        grounded = ground_text(text, evidence, deterministic)
        self.grounded[package_id] = grounded["text"]
        self.removed.extend(grounded["removed"])
        self.removed_by.setdefault(package_id, []).extend(grounded["removed"])
        if child["focus"] == "draft" and self.task_class == "business_research":
            published = _publish_plan(self, text)
            if published:
                child["report"] = published
                update_package(package_id, findings=published)
            elif _model_sections(text):
                child["report"] = text if "nothing was sent" in text.lower() else text.rstrip() + "\nNothing was sent.\n"
                update_package(package_id, findings=child["report"])
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
            repaired = repair.get("section") or ""
            if repaired:
                child["report"] = repaired
            elif not _protect_draft(self, child):
                child["report"] = render_focus(child["focus"], self._facts())
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
            if not _protect_draft(self, child):
                child["report"] = render_focus(child["focus"], self._facts())
            outcome = run_supervisor_attempts(lambda attempt: _decide(attempt, child["report"]) if attempt > 1 else "RETURN", limit=2)
            decision = outcome["decision"]
            if decision == "ACCEPT":
                update_package(package_id, findings=child["report"], attempt_count=outcome["attempts"], return_reason="")
            else:
                decision = "TAKE_OVER" if decision == "RETURN" else decision
                update_package(package_id, findings=child["report"], review_status=decision, attempt_count=outcome["attempts"], return_reason="Retry limit reached" if outcome["limited"] else "")
        elif first == "TAKE_OVER":
            if not _keep_model_draft(self, child) and not _protect_draft(self, child):
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
        from .deliverable import evidence_blob, plan_sections_filled

        drafts = [child.get("report") or "" for child in self.children if child.get("focus") == "draft"]
        self.research["objective"] = self.objective
        self.research["deliverable_filled"] = bool(drafts) and plan_sections_filled(
            drafts[0], evidence_blob(self.research.get("evidence")), self.objective,
        )
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
        self.resolution["rationale"] = rationale
        self.resolution["model_judgement"] = True
        decision = self.resolution["decision"]
        facts = self._facts()
        completion = score_task(self.task_class, render_parent(facts, self.audits, decision), self.research, self.quote)
        findings = render_parent(facts, self.audits, decision, completion)
        if self.task_class == "business_research" and drafts and (drafts[0] or "").strip():
            body = drafts[0].strip()
            findings = body if body.endswith("\n") else body + "\n"
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
            # Shared skill memory must not carry this job's queries, pages, or objective into the next one.
            memory.remember("DOMAIN", self.skills[0].name, f"{self.task_class}: manager {decision}.", provenance=f"package:{self.parent_id}", tags=self.task_class)
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
            "understood_objective": self.objective,
            "evidence_preview": [
                {
                    "url": item.get("source_url") or "",
                    "title": item.get("source_title") or "",
                    "text": clip_at_boundary(item.get("extracted_content") or "", 2000),
                }
                for item in (self.research.get("evidence") or [])
                if isinstance(item, dict)
            ],
            "relevant_opened": sum(1 for item in self.research.get("evidence") or [] if (item.get("metadata") or {}).get("relevant")),
            "filtered_results": self.research.get("filtered") or [],
            "targets": self.research.get("targets") or [],
            "failures": self.research.get("failures") or [],
            "research_budget": self.research.get("budget") or {},
            "research_gaps": self.research.get("gaps") or [],
            "fact_table": [
                {
                    "id": fact.get("id") or "",
                    "source": fact.get("source") or "",
                    "quantity": fact.get("quantity") or "",
                    "quote": (fact.get("quote") or "")[:400],
                }
                for fact in (self.research.get("fact_table") or [])
                if isinstance(fact, dict)
            ],
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
            bool(self.plan.get("human_approval_required"))
            and decision in ("ESCALATE", "RETURN", "RESEARCH_MORE", "CLARIFY", "SYNTHESISE", "ACCEPT")
        )
        final_state = state_for_manager(decision, approval_required=approval_required)
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
    import re

    from .calc import CalcError

    match = re.search(r"sum of\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)", objective or "", re.I)
    if match:
        expression = f"{match.group(1)}+{match.group(2)}"
    else:
        found = re.search(r"\d+(?:\.\d+)?(?:\s*[\+\-\*/]\s*\d+(?:\.\d+)?)+", objective or "")
        if not found:
            return None
        expression = re.sub(r"\s+", "", found.group(0))
    try:
        value = eval_arithmetic(expression)
    except CalcError:
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
            if role == "EMPLOYEE" and programme.task_class == "software_build":
                text, meta = _software_turn(programme, prompt["id"])
                programme.bind(role, prompt["id"], text, meta)
                continue
            limit = int(prompt.get("max_tokens") or (320 if role != "MANAGER" else 480))
            plan_write = False
            if role == "EMPLOYEE" and programme.task_class == "business_research":
                child = next((item for item in programme.children if item["id"] == prompt["id"]), None)
                plan_write = bool(child and child.get("focus") == "draft")
            text, _tokens, meta = _complete(role, prompt["system"], prompt["user"], max_tokens=limit, programme=programme, package_id=prompt["id"], plan=plan_write)
            if plan_write and _draft_was_cut(text, meta):
                text, meta = _continue_cut_draft(programme, prompt, text, meta)
            programme.bind(role, prompt["id"], text, meta)


def _draft_was_cut(text: str, meta: dict | None) -> bool:
    """A token limit that stops mid-sentence is not a finished draft."""
    if str((meta or {}).get("finish_reason") or "") == "length":
        return True
    body = (text or "").strip()
    if not body:
        return False
    last = body.splitlines()[-1].strip()
    if not last:
        return False
    return not re.search(r"[.!?]$", last) and last.lower() != "nothing was sent."


def _join_continuation(head: str, tail: str) -> str:
    """Drop the unfinished last line and keep the continuation that finishes the draft."""
    lines = (head or "").rstrip().splitlines()
    if lines and not re.search(r"[.!?]$", lines[-1].strip()):
        lines = lines[:-1]
    body = "\n".join(lines).rstrip()
    rest = (tail or "").strip()
    if not rest:
        return body
    if body and rest.lower().startswith(body.lower()[:80]):
        return rest if rest.endswith("\n") else rest + "\n"
    return (body + "\n" + rest).strip() + "\n"


def _continue_cut_draft(programme: Programme, prompt: dict, text: str, meta: dict) -> tuple[str, dict]:
    """Ask the model to finish a draft the token limit cut off. Do not invent the missing lines."""
    continued, _tokens, next_meta = _complete(
        "EMPLOYEE",
        prompt["system"],
        prompt["user"]
        + "\n\nThe draft stopped before it was finished. Continue from the last complete sentence. "
        + "Do not repeat earlier sections. Finish every heading through Unresolved. End with: Nothing was sent.\n\n"
        + "Draft so far:\n"
        + (text or ""),
        max_tokens=1200,
        programme=programme,
        package_id=prompt["id"],
        plan=True,
    )
    joined = _join_continuation(text, continued)
    kept = dict(next_meta or {})
    kept["continued_from"] = (meta or {}).get("finish_reason") or "cut"
    if not (continued or "").strip():
        return _join_continuation(text, ""), meta
    return joined, kept


def _software_turn(programme: Programme, package_id: str) -> tuple[str, dict]:
    from .software import build_deliverable

    def complete(system: str, user: str, max_tokens: int, prefill: str = "", ollama_format=None):
        from .software import PROGRAM_FORMAT, SOFTWARE_STOPS

        text, tokens, meta = _complete(
            "EMPLOYEE",
            system,
            user,
            max_tokens=max_tokens,
            programme=programme,
            package_id=package_id,
            plain=True,
            prefill=prefill,
            stop=list(SOFTWARE_STOPS),
            expand_unfinished=False,
            ollama_format=ollama_format or PROGRAM_FORMAT,
        )
        raw = meta.get("raw_response") if isinstance(meta.get("raw_response"), str) else (text or "")
        _record_generation(programme, package_id, "software", raw or "", meta, kept="generated")
        return text, tokens, meta

    try:
        text, meta = build_deliverable(programme.objective, complete)
        meta = dict(meta or {})
        meta.pop("raw_response", None)
    except Exception as exc:
        text = f"# Software deliverable\nTests: FAILED\n\n{type(exc).__name__}: {exc}\n\nNothing was sent.\n"
        meta = {"error": f"{type(exc).__name__}: {exc}", "backend": "software", "tests_passed": False}
    programme.research["tests_passed"] = bool(meta.get("tests_passed"))
    programme.research["mode"] = "software"
    return text, meta


def _model_sections(text: str) -> bool:
    from .deliverable import plan_sections_filled

    return plan_sections_filled(text or "")


def _keep_model_draft(programme: Programme, child: dict) -> bool:
    report = child.get("report") or ""
    if programme.task_class == "software_build" and child.get("focus") == "build":
        return "tests:" in report.lower()
    if programme.task_class != "business_research" or child.get("focus") != "draft":
        return False
    if _model_sections(report):
        return True
    from .deliverable import parse_sections

    sections = parse_sections(report)
    written = 0
    for body in sections.values():
        lowered = (body or "").lower()
        if len(body or "") < 40 or lowered.startswith("unresolved") or "only what that request" in lowered:
            continue
        written += 1
    return written >= 4


def _protect_draft(programme: Programme, child: dict) -> bool:
    """A business draft the model wrote is kept. The blank form is not a substitute."""
    if getattr(programme, "task_class", "") != "business_research" or child.get("focus") != "draft":
        return False
    report = child.get("report") or ""
    if report.startswith("Validation rejected this draft."):
        return True
    lowered = report.lower()
    if "facts come from the request or from a page that was opened" in lowered and "the service is only what that request" in lowered:
        return False
    return bool(report.strip())


def _publish_plan(self: Programme, model_text: str) -> str:
    from .deliverable import plan_sections_filled, publish_business_plan, evidence_blob, retained_draft

    published = publish_business_plan(self.objective, self.research, model_text)
    blob = evidence_blob(self.research.get("evidence"))
    if published and plan_sections_filled(published, blob, self.objective):
        return published
    import os

    from ..models import local_base

    if os.environ.get("AYVEN_LLM_STUB", "1") != "0" or not local_base():
        if (model_text or "").strip():
            return retained_draft(model_text, self.objective, blob, {})
        return ""
    from .deliverable import finished_lines, parse_sections

    sections = _join_advert(parse_sections(finished_lines(model_text or "")))
    reasons = _fill_plan_sections(self, sections, blob)
    assembled = _assemble_plan(sections, self.objective)
    published = publish_business_plan(self.objective, self.research, assembled)
    if published and plan_sections_filled(published, blob, self.objective):
        return published
    if plan_sections_filled(assembled, blob, self.objective):
        return assembled
    if (model_text or "").strip():
        return retained_draft(model_text, self.objective, blob, reasons, sections)
    return ""


def begin_clarification(package_id: str, answer: str) -> str:
    """Validate and record the answer. The HTTP handler returns before the work continues."""
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
    return package_id


_CLARIFICATION_WORKERS: dict[str, object] = {}


def start_clarification_worker(package_id: str):
    """Continue the package on a daemon thread, the same way a Milo job is started."""
    import threading

    thread = threading.Thread(target=continue_clarification, args=(package_id,), daemon=True, name=f"clarify-{package_id[:8]}")
    _CLARIFICATION_WORKERS[package_id] = thread
    thread.start()
    return thread


def wait_for_clarification(package_id: str, timeout: float = 90) -> bool:
    thread = _CLARIFICATION_WORKERS.get(package_id)
    if thread is None:
        return True
    thread.join(timeout)
    return not getattr(thread, "is_alive")()


def continue_clarification(package_id: str) -> str:
    """Finish the package that begin_clarification already accepted."""
    try:
        return _continue_clarification(package_id)
    except Exception as exc:
        try:
            log_transition(package_id, "FAILED", f"clarification continuation failed: {type(exc).__name__}")
            update_package(package_id, status="failed", findings=f"FAILED. {type(exc).__name__}: {exc}"[:800])
        except Exception:
            pass
        return package_id


def _continue_clarification(package_id: str) -> str:
    from .store import package

    row = package(package_id)
    if not row:
        raise KeyError("package not found")
    cleaned = (row.get("clarification_answer") or "").strip()
    if not cleaned:
        raise ValueError("answer is empty")
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


def answer_clarification(package_id: str, answer: str) -> str:
    """Synchronous continuation for a caller that already holds the worker thread."""
    begin_clarification(package_id, answer)
    return _continue_clarification(package_id)


def _approved_research_is_unresolved(row: dict, findings: str) -> bool:
    """Fixture or unopened research stays unresolved. Approval is not evidence.

    A calculation or a short acknowledgement has no research page to open, so
    approval can still complete that package.
    """
    task = (row.get("task_class") or "").lower()
    if task in {"calculation", "trivial"}:
        return False
    if task == "software_build":
        return "tests: passed" not in (findings or "").lower()
    text = (findings or "").lower()
    obs: dict = {}
    try:
        loaded = json.loads(row.get("observability_json") or "{}")
        if isinstance(loaded, dict):
            obs = loaded
    except json.JSONDecodeError:
        obs = {}
    research: dict = {}
    raw_research = row.get("research_json")
    if isinstance(raw_research, str) and raw_research:
        try:
            loaded = json.loads(raw_research)
            if isinstance(loaded, dict):
                research = loaded
        except json.JSONDecodeError:
            research = {}
    mode = str(obs.get("research_mode") or research.get("mode") or "").lower()
    if mode in {"fixtures", "fixture", "stub"}:
        return True
    if "no opened source" in text or ("fixtures" in text and mode != "live"):
        return True
    pages = [
        page
        for page in (obs.get("pages") or research.get("pages") or [])
        if isinstance(page, str) and page.startswith("http")
    ]
    research_like = "research" in task or task in {
        "web_research",
        "business_research",
        "football_tickets",
        "vending_prospects",
        "internal_door_quote",
    }
    if not research_like:
        return False
    if mode != "live" or not pages:
        return True
    if task == "business_research" and not _business_package_can_complete(row, findings, obs):
        return True
    contract = obs.get("contract_evaluation") or {}
    if isinstance(contract, dict) and contract.get("passed") is False:
        return True
    return False


def resume_approved_package(package_id: str) -> str:
    """Approval authorises the next internal step. It does not prove the job succeeded."""
    from .campus_view import mark_stage
    from .store import package

    row = package(package_id) or {}
    if (row.get("workflow_state") or "") != "APPROVED":
        return row.get("workflow_state") or ""
    findings = row.get("findings") or ""
    unsupported = _approved_research_is_unresolved(row, findings)
    log_transition(package_id, "ACTIONING", "Lee approved the internal review. Nothing is sent.")
    if unsupported:
        obs = {}
        raw_obs = row.get("observability_json") or ""
        if raw_obs:
            try:
                obs = json.loads(raw_obs)
            except json.JSONDecodeError:
                obs = {}
        pages = [page for page in (obs.get("pages") or []) if isinstance(page, str) and page.startswith("http")]
        if pages:
            note = "UNRESOLVED. Opened pages are recorded, and the plan is not usable yet.\n"
            summary = "Unresolved: the plan is not usable"
        else:
            note = "UNRESOLVED. Approval did not create evidence.\n"
            summary = "Unresolved: no opened sources"
        log_transition(package_id, "UNRESOLVED", summary)
        update_package(
            package_id,
            stage="results",
            status="unresolved",
            destination="command",
            next_action=summary,
            findings=note + findings,
        )
        mark_stage(package_id, "UNRESOLVED", project_id=row.get("project_id") or "", task_id=row.get("task_id") or "")
        _set_agent(MANAGER, visual_state="FAILED", status="idle", last_summary=summary, progress=1)
        _set_agent("milo", visual_state="WAITING", status="idle", last_summary="Package unresolved after approval", progress=1)
        if row.get("project_id"):
            conn = connect()
            conn.execute(
                "UPDATE projects SET status=?, result=? WHERE id=?",
                ("unresolved", "Approved, but research was not supported by opened sources.", row["project_id"]),
            )
            conn.commit()
            conn.close()
        return "UNRESOLVED"
    log_transition(package_id, "COMPLETED", "Approved package met its deliverable. Nothing was sent.")
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
            ("complete", findings or "Approved. Nothing was sent.", row["project_id"]),
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


def _domain_note_for_objective(objective: str, row: dict) -> bool:
    """A skill note may be reused. Another job's pages and URLs may not."""
    text = str(row.get("content") or "")
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered:
        return False
    from .deliverable import _blob_has_term, distinctive_terms

    terms = [term for term in distinctive_terms(objective) if len(term) >= 5]
    return any(_blob_has_term(term, lowered) for term in terms)


def _job_memory(programme: Programme) -> list[dict]:
    """This project's notes, plus a skill note that is actually about this request."""
    project_id = str(getattr(programme, "project_id", "") or "")
    parent_id = str(getattr(programme, "parent_id", "") or "")
    if not project_id and not parent_id:
        return []
    rows = memory.retrieve(getattr(programme, "objective", "") or "", limit=8)
    own = []
    for row in rows:
        scope = row.get("scope") or ""
        subject = str(row.get("subject_id") or "")
        if scope == "PROJECT" and project_id and subject == project_id:
            own.append(row)
        elif scope == "WORK_PACKAGE" and parent_id and subject == parent_id:
            own.append(row)
        elif scope == "DOMAIN" and _domain_note_for_objective(getattr(programme, "objective", "") or "", row):
            own.append(row)
    return own[:3]


def _employee_user(programme: Programme, child: dict) -> str:
    programme.memory_rows = _job_memory(programme)
    remembered = memory.format_for_prompt(programme.memory_rows)
    block = f"\n\n{remembered}" if remembered else ""
    plan = ""
    if programme.task_class == "business_research" and child.get("focus") != "draft":
        gaps = "; ".join((programme.research.get("gaps") or [])[:4])
        shown = (
            f"Opened pages:\n{_opened_index(programme) or 'None.'}\n"
            f"Prices from opened pages:\n{_price_notes(programme) or 'No page stated a £ figure.'}\n"
            f"Gaps: {gaps or 'none'}\n"
        )
    else:
        shown = f"Published draft:\n{clip_at_boundary(child['report'], 6000)}\n"
    if programme.task_class == "business_research" and child.get("focus") == "draft":
        shown = "No owner plan has been written yet. Do not copy a blank form.\n"
        plan = (
            "\n\nWrite the launch plan for the owner. Put each heading on its own line:\n"
            "Service\nTarget customer\nProblem\nOffer and positioning\n"
            "Competitor and market research\nPricing\nChannels\nAdvert\nCall to action\nNext steps\nAssumptions\nUnresolved\n"
            "Under Advert write three lines that start Headline:, Body:, and Call to action:.\n"
            "The name in the request may not appear on the opened pages. Still write the offer, the channels, the advert, and the next steps as recommendations.\n"
            "The offer says what is sold and how that differs from the alternatives named in the request. Use differs, instead, or without.\n"
            "Write each section once. Do not repeat a sentence. Do not narrate. Do not say I, let me, let's, I think, we need to, or the user.\n"
            "Do not quote a sentence from the objective. Do not name a person. Do not name a trade this request does not name.\n"
            "Use only the service in this request and the facts below.\n"
            "A sentence that states a number or a competitor name must cite [F#] and use that fact's quote. "
            "A recommendation may not contain a number or a name.\n"
            "An Assumption line states how the measured inputs become a price and has no £ figure. "
            "Use a unit only when a fact states that unit. "
            "Write not stated only when no fact states a £ figure for that part. "
            "On its own Assumption line, name what still has to be measured.\n"
            "Channels and next steps name a place and an action that fit this business.\n"
            "The offer names what is included and what the visit measures.\n"
            "A competitor line paraphrases the page and keeps the citation.\n"
            "Do not use a promise the request or the cited fact does not state.\n"
            "Write the advert lines. Do not describe the format.\n"
            "End with: Nothing was sent.\n\n"
            f"Request:\n{programme.objective[:800]}\n\n"
            + _section_evidence(programme)
        )
    return (
        "Ayven tool runtime. When you need a tool, emit a line "
        'TOOL ayven_tool {"tool":"record_review","payload":"why"} and then the answer.\n'
        f"Focus: {child['focus']}\nObjective:\n{programme.objective}\n\n{shown}{plan}{block}"
    )


def _opened_index(programme: Programme) -> str:
    lines = []
    for item in (programme.research.get("evidence") or [])[:16]:
        title = item.get("source_title") or "page"
        url = item.get("source_url") or ""
        if url:
            lines.append(f"- {title}: {url}")
    return "\n".join(lines)


def _price_notes(programme: Programme) -> str:
    import re

    notes = []
    for item in (programme.research.get("evidence") or [])[:16]:
        title = item.get("source_title") or "page"
        url = item.get("source_url") or ""
        text = item.get("extracted_content") or ""
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if "£" not in sentence and "$" not in sentence:
                continue
            notes.append(f"{title} ({url}): {clip_at_boundary(sentence, 240)}")
            break
    return "\n".join(notes)


def _component_price_block(programme: Programme) -> str:
    """Show the sentence the pricing check will count, once per part. Do not write the section."""
    from .deliverable import component_price_snippets

    rows = component_price_snippets(programme.research.get("evidence") or [], programme.objective)
    if not rows:
        return ""
    lines = []
    for item, snippet in rows:
        if snippet:
            lines.append(f"{item}: {snippet}")
        else:
            lines.append(f"{item}: no opened page states a £ figure.")
    return (
        "Market references found on opened pages. A figure is not a price for this service unless that page states the size or the spec.\n"
        + "\n".join(lines)
    )


def _section_jobs(objective: str) -> tuple:
    """One focused prompt per section. The stem is the start of the sentence, not a second trade."""
    from .deliverable import covered_items, measurements_still_needed, service_brief

    brief = service_brief(objective)
    parts = covered_items(objective)
    labeled = ", ".join(parts) if parts else "the service"
    measure = ", ".join(measurements_still_needed(objective))
    measure_line = (
        f" On a separate Assumption line, name what still has to be measured: {measure}."
        if measure
        else ""
    )
    shared = (
        f"\nRequest: {brief}\n"
        "Finish the sentence for the owner. Do not quote the request. Do not describe this task.\n"
    )
    # The request sentence is not repeated here. Copying it is what the echo check rejects.
    own = (
        f"\nService words: {labeled}.\n"
        "Finish the sentence for the owner. Do not quote the request. Do not begin with the request's sentence. Do not describe this task.\n"
    )
    return (
        ("service", "Service", "Write one sentence. Name the service. Use the request's own words. Do not use a business name that appears only on an opened page." + shared, "The service "),
        ("target customer", "Target customer", "Write one sentence. Name the customer." + shared, "The customer is "),
        ("problem", "Problem", "Write one or two sentences. Name the problem." + shared, "The problem is "),
        ("offer and positioning", "Offer and positioning", "Write two or three sentences. Name the offer. Name what the package includes, what it leaves out, and what the visit measures. Say how it differs. Do not write only that the offer is a package covering the parts. Do not invent a price. Do not use a business name that appears only on an opened page." + own, ""),
        ("competitor and market research", "Competitor and market research", "Write two or three sentences. Name two UK businesses from the fact table that sell the service. name one opened page for each business. Paraphrase what each offers. Put [F#] in the same sentence as the £ figure. Cite each business's own £ figure. A cost guide is not a competitor. Keep the quantity and the unit. If the fact states pay on completion, plus vat, week (minimum), or what the price excludes, copy those words into the same sentence. Do not copy a run of the page's words. Then say how this service differs from those two, using differs, unlike, or instead. Do not paste a page title or an opened-sources dump. Do not start a line with a heading from the page. A market report or a how-to guide is not a competitor. Do not invent a name." + own, ""),
        ("pricing", "Pricing", "Write one line for each market reference. Do not start with the request. The first line cites one fact whose quantity is a £ figure. Copy that quantity, its unit, and its [F#]. If the fact states pay on completion, plus vat, week (minimum), or what the price excludes, copy those words into the same sentence. If the fact does not give the size, the material, or the unit, say so in that sentence. Do not paste a page title. Then start each remaining line with Assumption. An Assumption line states how the measured inputs become a price, has no [F#], and has no £ figure. Use a unit only when a fact states that unit. When no fact states a £ figure for a part, write not stated. Do not invent a £ figure." + measure_line + " Parts: " + labeled + "." + own, ""),
        ("channels", "Channels", "Write one sentence for this business. Name the channel. The channel is where a customer hears about the service, not the place the work is done. Use one of these places in the sentence: Facebook, Instagram, directory, leaflet, website, workshop, or Google. Do not name a showroom unless the request names one. Name the action the owner takes there, and say first week or first month. Listing a price, a rental rate, or a first-week rental is not the channel. The action has to fit this service, so the same sentence would not fit a different trade. Do not say online platforms, industry reports, conduct market research, or asks for a reply. Do not name a competitor's business as the channel. Do not name the person who wrote the request. Do not mention hours." + own, ""),
        ("advert", "Advert", "Write exactly three lines and stop.\nHeadline:\nBody:\nCall to action:\nThe headline is a complete phrase under 10 words. Do not start the headline with Ask about.\nThe call to action asks the customer to get in touch. Do not promise a free quote or a free trial.\nDo not paste a machine specification or a size.\nDo not use a business name that appears only on an opened page.\n" + own, ""),
        ("call to action", "Call to action", "Write one sentence. Ask the reader to get in touch. Do not name the person who wrote the request. Do not use a business name that appears only on an opened page." + own, "Ask "),
        ("next steps", "Next steps", "Write two sentences for this business. Name the owner's next action. These are actions the owner takes to launch the business and win the first customers. Do not describe the owner buying or receiving the service. Do not install the work in the owner's own home or office. Do not promise a free trial. In the first week, name one practical action and the place the owner does it. In the first month, name the next action. The actions have to fit this service, so the same sentences would not fit a different trade. Do not say conduct market research. Do not name a service area. Do not name a competitor's business as the place the owner goes. Do not name the person who wrote the request." + own, ""),
        ("assumptions", "Assumptions", "Write one sentence. Name one assumption." + shared, "The plan assumes "),
        ("unresolved", "Unresolved", "Write one sentence. Name one open fact. Name a gap the opened pages do not settle. Do not repeat a fact the request already states." + shared, "Still open: "),
    )


def _section_budget(key: str, attempt: int) -> tuple[int, list[str]]:
    """Enough tokens for a short note and the sentence. A newline stop makes Ollama return nothing."""
    if key in {"advert", "pricing", "competitor and market research"}:
        return (480 if attempt > 1 else 640), []
    return 480, []


def _join_advert(sections: dict) -> dict:
    """The call to action is part of the advert when the parser stored it under its own heading."""
    joined = dict(sections or {})
    advert = (joined.get("advert") or "").strip()
    cta = (joined.get("call to action") or "").strip()
    if advert and cta and "call to action" not in advert.lower():
        joined["advert"] = advert + "\nCall to action: " + cta
    return joined


def _section_missing(key: str, body: str, blob: str, objective: str = "") -> bool:
    return bool(_section_drop_reason(key, body, blob, objective))


def _section_body(text: str, kind: str, objective: str = "") -> str:
    """Owner sentences only. A prompt essay is discarded here, before it can be stored."""
    from .deliverable import owner_section

    return owner_section(kind, text or "")


def _section_drop_reason(key: str, body: str, blob: str, objective: str) -> str:
    """Why a reply did not become the stored section. Empty when the check passed."""
    from .deliverable import section_rejection

    return section_rejection(key, body, blob, objective)


def _record_generation(programme: Programme, package_id: str, kind: str, raw: str, meta: dict, kept: str = "", attempt: int = 0) -> None:
    if not package_id:
        return
    payload = dict(meta or {})
    payload["raw_response"] = raw or payload.get("raw_response") or ""
    save_model_call(package_id, "EMPLOYEE", getattr(programme, "task_class", "") or "", payload, payload["raw_response"])
    from .observability import record_trace

    record_trace(package_id, "generation", {
        "kind": kind,
        "finish_reason": payload.get("finish_reason") or "",
        "prompt_tokens": int(payload.get("prompt_tokens") or 0),
        "completion_tokens": int(payload.get("completion_tokens") or 0),
        "elapsed_s": payload.get("elapsed_s") or 0,
        "num_ctx": int(payload.get("num_ctx") or (payload.get("constrained") or {}).get("num_ctx") or 0),
        "prompt_chars": int(payload.get("prompt_chars") or (payload.get("constrained") or {}).get("prompt_chars") or 0),
        "kept": kept,
        "reason": payload.get("reason") or "",
        "response": payload["raw_response"],
        "attempt": int(attempt or payload.get("attempt") or 0),
        "prompt_eval_s": float(payload.get("prompt_eval_s") or (payload.get("constrained") or {}).get("prompt_eval_s") or 0),
        "eval_s": float(payload.get("eval_s") or (payload.get("constrained") or {}).get("eval_s") or 0),
        "load_s": float(payload.get("load_s") or (payload.get("constrained") or {}).get("load_s") or 0),
        "error": payload.get("error") or "",
    })


def _check_for_model(reason: str) -> str:
    """The retry names the check. It does not hand a rejected claim back to be copied."""
    text = (reason or "").strip() or "it did not pass validation"
    lowered = text.lower()
    if "24/7" in lowered or "always ready" in lowered or "availability" in lowered:
        return "The previous line was an availability claim. That claim is not stored."
    if "cut off" in lowered:
        return "Write the whole sentence again. Do not leave a fragment."
    if "showroom" in lowered:
        return "The request does not name a showroom. Name a channel this business can use: Facebook, a directory, a leaflet, a website, or a workshop."
    if "cost guide" in lowered:
        return "A cost guide is not a competitor. Name two businesses that sell the service, and cite each business's £ figure."
    if "opened business page" in lowered:
        return "Cite the £ figure from the opened business page in the competitor comparison. A cost guide is not a competitor."
    if "already in the request" in lowered:
        return "That fact is already in the request. Name a gap the opened pages do not settle."
    if "as the customer" in lowered or "first customers" in lowered:
        return "Name an action the owner takes to launch the business and win the first customers. Do not describe the owner buying or receiving the service."
    if "listing a price" in lowered:
        return "Name how a customer hears about the service. Listing a price is not the channel."
    if "comes from an opened page" in lowered:
        return "Do not use that business name. It is on an opened page. Write the sentence with the request's own words."
    if "the claim uses" in lowered:
        return "Do not use the words the check named. Use the opened page's own words for what the £ buys, and keep [F#] in that sentence."
    if "gives the unit" in lowered:
        stated = re.search(r"opened page states '([^']+)'", text, re.I)
        if stated:
            return (
                f"Use '{stated.group(1)}', the unit written next to that £ figure on the opened page. "
                "Keep the citation."
            )
        return "Use the unit written next to that £ figure on the opened page. Keep the citation."
    if "does not say how this service differs" in lowered:
        return "Say how this service differs from the two businesses. Use differs, unlike, or instead."
    if "promises '" in lowered:
        return "The previous line promised something the request does not say. Do not repeat that promise."
    if "does not ask the customer" in lowered or "names the person who wrote" in lowered:
        return "Ask the reader to get in touch. Do not name the person who wrote the request."
    if "repeats the objective" in lowered:
        return "Do not copy the request. Write this section in your own sentences."
    if "the claim does not" in lowered:
        return "Use that page's £ figure and the condition stated with it, such as pay on completion or what the price excludes. Keep the citation. Paraphrase. Do not copy a run of the page's words."
    if "shared form" in lowered:
        return "Name a place and an action that fit this business. A sentence that would fit a different trade is not stored."
    if "does not name a timeframe" in lowered:
        return "The channel and the action can stay. Say first week or first month in the same sentence."
    if "does not name a practical action" in lowered:
        return "Name the action the owner takes on that channel. Say first week or first month."
    if "does not name a channel" in lowered or "timeframe" in lowered:
        return (
            "Name where a customer hears about the service: Facebook, a directory, "
            "a leaflet, a website, or a workshop. Name the action and say first week or first month. "
            "Do not name a showroom unless the request names one. Do not name a competitor."
        )
    if "one source" in lowered or "which opened page" in lowered:
        return "Put each £ figure in its own sentence with that figure's own opened page and [F#]. One claim, one source."
    if "fewer than two" in lowered:
        return "Name two opened businesses and what each offers. Say how this service differs. Do not invent a name."
    if "worksheet" in lowered or "too long to be a finished answer" in lowered:
        return "Keep the section short. For pricing, one £ line with its [F#] and one Assumption line."
    if "does not cite a £" in lowered:
        return "Put [F#] in the same sentence as the £ figure. Keep the quantity and the unit."
    if "page heading" in lowered or "pastes the page" in lowered:
        return "Paraphrase the page. Do not copy a run of its words. Keep the citation and the £ figure."
    if "page title" in lowered or "opened page text" in lowered:
        return "Do not paste a page title or an opened-sources dump. Say what the figure buys, or that the page does not give the unit."
    if "not a complete phrase" in lowered or "fragment" in lowered or "ask about" in lowered:
        return "Write a complete headline under 10 words. Do not start with Without or Ask about."
    if "assumption line restates" in lowered:
        return "Keep the cited fact on its own line. An Assumption line has no [F#] and no £ figure."
    if "prioritised" in lowered or "first month" in lowered:
        return "Name the action for the first week and the action for the first month."
    if "quotes a percentage" in lowered:
        return "Name what is costly or disruptive for the customer. Do not quote a percentage."
    if "does not name the package" in lowered or "generic sentence" in lowered or "visit measures" in lowered:
        return "Name what the package includes, what the visit measures, and how it differs."
    if "turn the measurements" in lowered:
        return "State how the measured inputs become a price. Use a unit only when a fact states that unit. The Assumption line has no £ figure."
    if "without a valid" in lowered or "had no [f#]" in lowered:
        return "Cite the fact as [F#] and use that fact's quantity, unit, and quote. An Assumption line has no £ figure."
    if "does not use it" in lowered:
        return "Cite the opened £ figure as [F#] on its own line. Then write an Assumption line for the method, with no £ figure."
    return text


def _section_prompt(instruction: str, attempt: int, reason: str, prices: str, page_line: str = "") -> str:
    """Evidence stays the prefix so a later section reuses it. The retry names the failed check."""
    if attempt <= 1:
        tail = instruction
    else:
        shown = page_line.strip()
        extra = f"{shown}\n" if shown else ""
        tail = (
            "The previous answer was rejected and was not stored.\n"
            f"Check: {_check_for_model(reason)}\n"
            f"{extra}"
            "Write the finished lines only. Do not repeat a rejected claim.\n"
            + instruction
        )
    if prices:
        return f"{prices}\n{tail}"
    return tail


def evidence_pack_mode() -> str:
    """full is the install behaviour. compact is the eval's prompting comparison."""
    import os

    mode = (os.environ.get("AYVEN_EVIDENCE_PACK") or "full").strip().lower()
    return "compact" if mode == "compact" else "full"


def _compact_facts(programme: Programme) -> str:
    """One short line per opened page. The instruction text is unchanged."""
    lines = []
    for item in (programme.research.get("evidence") or [])[:6]:
        title = item.get("source_title") or "page"
        url = item.get("source_url") or ""
        text = item.get("extracted_content") or ""
        figure = ""
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if "£" in sentence or "$" in sentence:
                figure = clip_at_boundary(sentence.strip(), 160)
                break
        if figure:
            lines.append(f"- {title}: {figure}")
        elif url:
            lines.append(f"- {title}: {url}")
    return "\n".join(lines)


def _ensure_facts(programme: Programme) -> list[dict]:
    """Extract the fact table once. The model labels relevance when a model is available."""
    if programme.research.get("facts_labeled") and isinstance(programme.research.get("fact_table"), list):
        return programme.research.get("fact_table") or []
    from .facts import extract_facts, label_relevance

    facts = extract_facts(programme.research.get("evidence") or [])

    def complete(role, system, user, max_tokens=240, plain=True, plan=True):
        return _complete(
            role, system, user, max_tokens=max_tokens, programme=programme,
            package_id=getattr(programme, "parent_id", "") or "", plain=plain, plan=plan,
        )

    facts = label_relevance(facts, programme.objective, complete)
    programme.research["fact_table"] = facts
    programme.research["facts_labeled"] = True
    return facts


def _section_evidence(programme: Programme, key: str = "") -> str:
    """The writing prompt gets the fact table and the request. Not the raw page text."""
    from .facts import render_fact_table

    block = "\n" + render_fact_table(_ensure_facts(programme))
    if key == "pricing":
        block += (
            "Cite [F#] on every market reference. Use that fact's own words for the quantity, the unit, and what it includes. "
            "An Assumption line states the method and has no £ figure. "
            "When no fact states a £ figure for a part, write not stated.\n"
        )
    return block


def _write_one_section(programme: Programme, key: str, title: str, instruction: str, prefill: str = "", attempt: int = 1, reason: str = "") -> tuple[str, str, dict]:
    prices = _section_evidence(programme, key)
    from .deliverable import evidence_blob, page_line_for_check

    page_line = page_line_for_check(reason, evidence_blob(programme.research.get("evidence")))
    user = _section_prompt(instruction, attempt, reason, prices, page_line)
    limit, stop = _section_budget(key, attempt)
    text, _tokens, meta = _complete(
        "EMPLOYEE",
        "Write the finished lines only.",
        user,
        max_tokens=limit,
        programme=programme,
        package_id=programme.parent_id,
        plain=True,
        prefill=prefill,
        stop=stop,
        plan=True,
    )
    raw = meta.get("raw_response") if isinstance(meta.get("raw_response"), str) and meta.get("raw_response") else (text or "")
    return _section_body(text, key, programme.objective), raw, meta


def _has_model_sentence(text: str) -> bool:
    """True when Qwen wrote an owner sentence, rather than the blank form."""
    from .deliverable import _body_filled, parse_sections

    if "No opened page stated an offer" in (text or "") and "Headline:" not in (text or ""):
        return False
    sections = parse_sections(text or "")
    for key in ("offer and positioning", "pricing", "advert", "channels", "next steps"):
        if _body_filled(sections.get(key) or ""):
            return True
    return False


def _advert_saved_lines(raw: str) -> tuple[str, str]:
    """Body and call to action that already pass, when the headline does not."""
    from .deliverable import _ASK, _advert_content_ok, _visible_answer

    body = cta = ""
    for line in _visible_answer(raw or "").splitlines():
        match = re.match(r"^(body|call to action)\s*:\s*(.+)$", line.strip(), re.I)
        if not match:
            continue
        label = match.group(1).lower()
        content = match.group(2).strip().strip('"')
        if not _advert_content_ok(label, content):
            continue
        if label == "body" and not body:
            body = content
        elif label == "call to action" and not cta:
            if re.search(r"free quote|free consultation|in one day|same day|guaranteed", content, re.I):
                continue
            if not _ASK.search(content):
                continue
            cta = content
    return body, cta


def _clean_headline(raw: str) -> str:
    """A headline line that passes the same check as a stored advert line."""
    from .deliverable import _advert_content_ok, _headline_problem, _visible_answer

    for line in _visible_answer(raw or "").splitlines():
        stripped = line.strip().strip('"')
        match = re.match(r"^headline\s*:\s*(.+)$", stripped, re.I)
        content = match.group(1).strip() if match else stripped
        if _advert_content_ok("headline", content) and not _headline_problem(content):
            return content
    return ""


def _prepare_cited(programme: Programme, text: str) -> str:
    from .facts import enforce_citations

    return enforce_citations(text or "", _ensure_facts(programme), programme.objective)


_PRICE_SECTIONS = {"pricing", "competitor and market research"}


def _citation_gap(reason: str) -> bool:
    """True when the failure is only a missing citation."""
    lowered = (reason or "").lower()
    if not lowered:
        return True
    gaps = (
        "had no [f#]",
        "without a valid [f#]",
        "does not cite a £",
        "without naming the opened page",
        "not on an opened page",
        "not labelled as an assumption",
        "the model returned nothing",
        "sentence filter removed",
        "did not pass the sentence check",
    )
    return any(gap in lowered for gap in gaps)


def _sentences_without_uncited_price(text: str) -> str:
    """Sentences that do not carry an uncited £. Used to name the next check, not to store a cut line."""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        if "£" in sentence and not re.search(r"\[F\d+\]", sentence, re.I):
            continue
        kept.append(sentence)
    return "\n".join(kept).strip()


def _delete_uncited_pound(text: str) -> str:
    """Drop a sentence that states an uncited £. Do not cut the £ out of the sentence."""
    return _sentences_without_uncited_price(text)


def _cite_and_repair(programme: Programme, key: str, text: str, blob: str, raw: str = "") -> str:
    """Cite facts, then name the page on a £ line. A 24/7 reply is left for the retry."""
    cited = _prepare_cited(programme, text)
    source = f"{raw or ''}\n{text or ''}"
    if re.search(r"24/7|always ready", source, re.I):
        return cited
    from .facts import repair_before_gate

    return repair_before_gate(key, cited, _ensure_facts(programme), programme.objective, blob)


def _fill_plan_sections(programme: Programme, sections: dict, blob: str) -> dict:
    """Ask for each missing section on its own, at most three times. Return the reason each check still failed."""
    jobs = _section_jobs(programme.objective)
    for key, _title, _instruction, _prefill in jobs:
        raw = sections.get(key) or ""
        if not raw.strip():
            continue
        cleaned = _cite_and_repair(programme, key, _section_body(raw, key, programme.objective), blob, raw)
        if cleaned and not _section_missing(key, cleaned, blob, programme.objective):
            sections[key] = cleaned
        else:
            sections[key] = ""
    rejected: dict[str, str] = {}
    advert_rest = ("", "")
    hours_rejected = False
    for _pass in range(3):
        pending = [job for job in jobs if _section_missing(job[0], sections.get(job[0]) or "", blob, programme.objective)]
        if not pending:
            return {}
        for key, title, instruction, prefill in pending:
            attempt = _pass + 1
            use_instruction = instruction
            use_prefill = prefill
            if key == "advert" and attempt > 1 and advert_rest[0] and advert_rest[1]:
                use_instruction = "Write one line only. Start with Headline:. Name the result for the customer."
                use_prefill = "Headline: "
            elif key == "channels" and attempt > 1 and (
                hours_rejected or "24/7" in (rejected.get(key) or "").lower() or "availability" in (rejected.get(key) or "").lower()
            ):
                use_prefill = "The owner uses "
                use_instruction = instruction + " Do not claim that the service is available at all hours."
            elif key == "channels" and attempt > 1 and (
                "names the person" in (rejected.get(key) or "").lower()
                or "comes from an opened page" in (rejected.get(key) or "").lower()
            ):
                use_prefill = "The owner uses "
                use_instruction = instruction + " Do not name the person who wrote the request. Do not use a business name from an opened page."
            elif key == "next steps" and attempt > 1 and "names the person" in (rejected.get(key) or "").lower():
                use_prefill = "In the first week the owner "
                use_instruction = instruction + " Do not name the person who wrote the request."
            elif key == "channels" and attempt > 1 and (
                "does not name a channel" in (rejected.get(key) or "").lower()
                or "shared form" in (rejected.get(key) or "").lower()
            ):
                use_prefill = ""
                use_instruction = instruction + " Name the place and the action for this business."
            written, raw, meta = _write_one_section(
                programme, key, title, use_instruction, prefill=use_prefill, attempt=attempt, reason=rejected.get(key, ""),
            )
            if key == "advert" and attempt > 1 and advert_rest[0] and advert_rest[1]:
                headline = _clean_headline(raw)
                if headline:
                    written = f"Headline: {headline}\nBody: {advert_rest[0]}\nCall to action: {advert_rest[1]}"
            cited = _cite_and_repair(programme, key, written, blob, raw)
            raw_reason = _section_drop_reason(key, raw or "", blob, programme.objective)
            if written and not cited:
                written = ""
                if raw_reason:
                    reason = raw_reason
                else:
                    reason = "The lines stated a number or a name without a valid [F#]. They were not stored."
            elif raw_reason and ("24/7" in (raw or "").lower() or "always ready" in (raw or "").lower()):
                written = cited
                reason = raw_reason
            else:
                written = cited
                reason = "" if written and not _section_missing(key, written, blob, programme.objective) else _section_drop_reason(key, raw if not written else written, blob, programme.objective)
            if reason and raw_reason and (
                "promises '" in raw_reason.lower()
                or "does not ask the customer" in raw_reason.lower()
                or "complete phrase" in raw_reason.lower()
                or "fragment" in raw_reason.lower()
                or "cut off" in raw_reason.lower()
            ):
                reason = raw_reason
            elif (
                reason
                and "£" in (raw or "")
                and "£" not in (written or "")
                and not re.search(r"\[F\d+\]", raw or "", re.I)
            ):
                # An uncited £ drops the whole sentence. The retry hears the check that remains.
                if key in _PRICE_SECTIONS and _citation_gap(raw_reason):
                    reason = "The £ line had no [F#], so it was not stored. Cite that fact as [F#] and keep the quantity and the unit."
                elif key in _PRICE_SECTIONS:
                    reason = raw_reason or reason
                else:
                    other_source = _sentences_without_uncited_price(raw or "")
                    other = ""
                    if other_source:
                        other = _section_drop_reason(
                            key,
                            _section_body(other_source, key, programme.objective),
                            blob,
                            programme.objective,
                        )
                    if other and not _citation_gap(other):
                        reason = other
                    elif raw_reason and not _citation_gap(raw_reason):
                        reason = raw_reason
                    else:
                        reason = "The £ line had no [F#], so it was not stored. Cite that fact as [F#] and keep the quantity and the unit."
            if (
                reason
                and raw_reason
                and "does not cite a £" in reason.lower()
                and not _citation_gap(raw_reason)
            ):
                reason = raw_reason
            meta = dict(meta or {})
            meta["reason"] = reason
            _record_generation(programme, programme.parent_id or "", f"section:{key}", raw, meta, kept="" if reason else "kept", attempt=attempt)
            if reason:
                rejected[key] = reason
                if key == "channels" and ("24/7" in reason.lower() or "availability" in reason.lower()):
                    hours_rejected = True
                if key == "advert":
                    body, cta = _advert_saved_lines(raw)
                    if body and cta:
                        advert_rest = (body, cta)
                continue
            sections[key] = written
            rejected.pop(key, None)
    return rejected


def _assemble_plan(sections: dict, objective: str = "") -> str:
    from .deliverable import without_closing_line

    lines = ["Business launch plan", ""]
    for key, title, _instruction, _prefill in _section_jobs(objective):
        lines.append(title)
        body = without_closing_line(sections.get(key) or "")
        if body:
            lines.append(body)
        lines.append("")
    lines.append("Nothing was sent.")
    return "\n".join(lines).strip() + "\n"


def _business_repair_user(programme: Programme, previous: str) -> str:
    from .deliverable import finished_lines

    usable = finished_lines(previous or "")
    return (
        "Rewrite the launch plan as finished sentences for the owner. Output only the headings and the sentences.\n"
        "Headings on their own lines: Service, Target customer, Problem, Offer and positioning, "
        "Competitor and market research, Pricing, Channels, Advert, Call to action, Next steps, Assumptions, Unresolved.\n"
        "Do not repeat a sentence. Do not narrate. Do not say I, let me, or I need to.\n"
        "Do not write that the evidence does not state a section. Write the section.\n"
        "Offer and positioning: what the request sells, and how that differs from the alternatives the request names.\n"
        "Pricing: cite the £ figures below and start with ASSUMPTION: and which figure to plan around, and why.\n"
        "Advert: exactly three lines, Headline:, Body:, and Call to action:.\n"
        "Channels and next steps: full sentences about how the owner reaches the customer for this request.\n"
        "End with: Nothing was sent.\n\n"
        f"Objective:\n{programme.objective}\n\n"
        f"{_section_evidence(programme)}"
        "A sentence that states a number or a competitor name must cite [F#]. "
        "An Assumption line has no £ figure.\n\n"
        f"Sentences already written, with the worksheet removed:\n{usable or 'None.'}\n"
    )


def _business_package_can_complete(row: dict, findings: str, obs: dict) -> bool:
    from .deliverable import business_can_complete

    understood = str(obs.get("understood_objective") or "")
    if not understood:
        understood = row.get("objective") or ""
        answer = row.get("clarification_answer") or ""
        if answer:
            understood = understood.rstrip() + "\nLee answered: " + answer
    previews = obs.get("evidence_preview")
    if not isinstance(previews, list):
        previews = []
    return business_can_complete(findings or "", previews, understood)


def _supervisor_system() -> str:
    return (
        "You are the Ayven supervisor. Try to disprove material employee claims before you accept them. "
        "Check arithmetic, URLs, entailment, and unsupported specificity. "
        "First line is one of ACCEPT, RETURN, TAKE_OVER, ESCALATE. "
        "Do not repeat the employee if the evidence disagrees. Do not emit think tags."
    )


def _supervisor_user(programme: Programme, child: dict) -> str:
    claims = claim_ledger.list_claims(child["id"])
    brief = "\n".join(f"- {c['status']} {c['claim_type']}: {c['claim_text'][:180]}" for c in claims[:8])
    gaps = "; ".join((programme.research.get("gaps") or [])[:4])
    limit = 1200 if programme.task_class == "business_research" else 6000
    return (
        f"Objective:\n{programme.objective}\n\nPlan class: {programme.task_class}\n"
        f"Gaps: {gaps}\n\nDraft:\n{clip_at_boundary(child['report'], limit)}\n\nClaims:\n{brief}"
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
        "why": (reason or "A person must review this internal step. No external action is implemented.")[:400],
        "if_approved": "The same package is reviewed again. Approval does not prove the job succeeded, and nothing is sent.",
        "if_rejected": "The work package stops as rejected. Nothing is sent.",
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
    }.get(task_class, "Review this internal result. No external action is implemented, and nothing was sent.")
