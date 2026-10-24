"""Typed requests the runtime is allowed to execute.

Models may propose these objects. Free text, including reasoning, is not a request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchTarget(BaseModel):
    entity: str
    entity_type: str = "topic"
    requested_information: str = ""
    likely_source_type: str = "UNKNOWN"


class ResearchPlan(BaseModel):
    objective: str = ""
    targets: list[ResearchTarget] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    stop_when: str = ""


class SearchRequest(BaseModel):
    query: str
    objective: str = ""
    entities: list[str] = Field(default_factory=list)
    expected_source_type: str = "UNKNOWN"
    reason: str = ""


class FetchRequest(BaseModel):
    url: str
    objective: str = ""
    reason: str = ""


class BrowserRequest(BaseModel):
    url: str
    objective: str = ""
    reason: str
    interaction_needed: bool = False


class CalculationRequest(BaseModel):
    expression: str
    reason: str = ""


class ClaimProposal(BaseModel):
    claim: str
    claim_type: Literal[
        "FACT",
        "INFERENCE",
        "RECOMMENDATION",
        "DETERMINISTIC",
        "CALCULATION",
        "ASSUMPTION",
        "AMBIGUITY",
        "MISSING_INFORMATION",
        "ROUTE",
        "PROSPECT",
        "SOURCE_FAILURE",
    ] = "FACT"
    evidence_quote: str = ""
    source_url: str = ""


class MemoryProposal(BaseModel):
    content: str
    scope: Literal["WORK_PACKAGE", "PROJECT", "CUSTOMER", "DOMAIN", "COMPANY"]
    provenance: str
    confidence: float = 0.5
    kind: Literal["verified_fact", "preference", "decision", "procedure", "approved_knowledge", "unresolved"] = "verified_fact"


class SupervisorDecision(BaseModel):
    decision: Literal["ACCEPT", "RETURN", "TAKE_OVER", "ESCALATE"]
    rationale: str = ""


class ManagerDecision(BaseModel):
    decision: Literal["ACCEPT", "REPAIR", "RESEARCH_MORE", "CLARIFY", "TAKE_OVER", "ESCALATE", "APPROVAL_REQUIRED", "SYNTHESISE", "RETURN"]
    rationale: str = ""


class ApprovalRequest(BaseModel):
    summary: str
    package_id: str = ""
    reason: str = ""


class EmployeeSelfCheck(BaseModel):
    objective_answered: bool
    missing_items: list[str] = Field(default_factory=list)
    claims_lacking_evidence: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    numbers_from_calculator: bool = True
    urls_not_opened: list[str] = Field(default_factory=list)
    organisations_not_evidenced: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    more_research_needed: bool = False


class RepairInstruction(BaseModel):
    action: Literal[
        "RESEARCH_MORE",
        "REPLACE_SOURCE",
        "RECALCULATE",
        "REMOVE_CLAIM",
        "DOWNGRADE_TO_INFERENCE",
        "ASK_CLARIFICATION",
        "REWRITE",
        "EMPLOYEE_RETRY",
        "UNRESOLVED_GAP",
    ]
    claim_id: str = ""
    detail: str = ""
