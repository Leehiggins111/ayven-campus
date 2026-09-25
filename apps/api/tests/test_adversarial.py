"""Adversarial checks for the pre-GPU hardening pass. No GPU and no paid API."""

import uuid

import pytest

from app.db import connect
from app.intelligence.assertions import TRADES, VENDING, evaluate_project
from app.intelligence.audit import (
    advisory_decision,
    authoritative_decision,
    challenge_material_claims,
    run_supervisor_attempts,
)
from app.intelligence.calc import CalcError, eval_arithmetic
from app.intelligence.claims import add_claim, evidence_entails
from app.intelligence.completion import score_task
from app.intelligence.execution import Programme, run_objective
from app.intelligence.grounding import classify_sentence, ground_text
from app.intelligence.permissions import PermissionDenied, authorize
from app.intelligence.quoting import quote_internal_doors, require_amount, vat_treatment
from app.intelligence.research import (
    detect_conflicts,
    research,
    snippet_contradicts,
    unsupported_reseller_claim,
)
from app.intelligence.resolution import resolve_manager
from app.models import set_role_generator


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-09-25T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    run_objective(project_id, objective, None)
    return project_id


def _package() -> str:
    return str(uuid.uuid4())


SCENARIO = "\n".join([
    "1533.00",
    "963.00",
    "Labour unit: AMBIGUOUS.",
    "VAT was not applied.",
    "These totals are not a final quote.",
])


def test_supervisor_accepts_a_sound_scenario():
    assert authoritative_decision(SCENARIO, "internal_door_quote", "scenarios", 1) == "ACCEPT"


def test_supervisor_returns_unsupported_and_arithmetic_and_missing():
    assert authoritative_decision("No totals here.", "internal_door_quote", "scenarios", 1) == "RETURN"
    assert authoritative_decision("The total is 10.00 and nothing else.", "internal_door_quote", "scenarios", 1) == "RETURN"
    assert authoritative_decision("All set.", "football_tickets", "gaps", 1) == "RETURN"


def test_supervisor_take_over_and_escalate():
    assert authoritative_decision("The final quote is £10.", "internal_door_quote", "scenarios", 1) == "TAKE_OVER"
    assert authoritative_decision(SCENARIO, "internal_door_quote", "scenarios", 1, conflicts=[{"key": "x"}]) == "ESCALATE"


def test_model_accept_cannot_outvote_a_deterministic_return():
    advisory = advisory_decision("ACCEPT\nlooks fine")
    deterministic = authoritative_decision("No totals here.", "internal_door_quote", "scenarios", 1)
    assert advisory == "ACCEPT"
    assert deterministic == "RETURN"
    assert advisory != deterministic


def test_model_return_cannot_outvote_sound_evidence():
    advisory = advisory_decision("RETURN\nI dislike the tone")
    deterministic = authoritative_decision(SCENARIO, "internal_door_quote", "scenarios", 1)
    assert advisory == "RETURN"
    assert deterministic == "ACCEPT"


def test_retry_return_then_accept_and_double_return_and_limit():
    calls = {"n": 0}

    def decide(attempt):
        calls["n"] += 1
        return "RETURN" if attempt == 1 else "ACCEPT"

    once = run_supervisor_attempts(decide, limit=2)
    assert once["path"] == ["RETURN", "ACCEPT"]
    assert once["limited"] is False

    twice = run_supervisor_attempts(lambda _attempt: "RETURN", limit=2)
    assert twice["path"] == ["RETURN", "RETURN"]
    assert twice["limited"] is True
    assert twice["attempts"] == 2
    assert calls["n"] == 2


def test_manager_synthesise_clarify_escalate_and_conflicts():
    synthesise = resolve_manager(task_class="trivial", audits=[{"focus": "trivial", "decision": "ACCEPT"}], claims=[], quote=None, research={"evidence": [], "rounds_exhausted": True})
    assert synthesise["decision"] == "SYNTHESISE"
    assert synthesise["resolution_method"] == "evidence"

    clarify = resolve_manager(
        task_class="internal_door_quote",
        audits=[{"focus": "scenarios", "decision": "ACCEPT", "advisory": "ACCEPT"}],
        claims=[],
        quote={"labour_unit": "AMBIGUOUS"},
        research={"evidence": [], "rounds_exhausted": True, "gaps": []},
    )
    assert clarify["decision"] == "CLARIFY"
    assert clarify["resolution_method"] == "customer_clarification"

    escalate = resolve_manager(
        task_class="trivial",
        audits=[{"focus": "trivial", "decision": "ESCALATE"}],
        claims=[],
        quote=None,
        research={"rounds_exhausted": True},
    )
    assert escalate["decision"] == "ESCALATE"
    assert escalate["resolution_method"] == "unresolved"

    contradiction = resolve_manager(
        task_class="football_tickets",
        audits=[{"focus": "routes", "decision": "ACCEPT"}],
        claims=[{"status": "CONTRADICTED", "claim_type": "FACT", "supersedes": ""}],
        quote=None,
        research={"evidence": [{"source_url": "https://www.bvb.de/x"}], "rounds_exhausted": True, "gaps": []},
    )
    assert contradiction["decision"] == "ESCALATE"

    insufficient = resolve_manager(
        task_class="vending_prospects",
        audits=[{"focus": "prospects", "decision": "ACCEPT"}],
        claims=[],
        quote=None,
        research={"evidence": [], "rounds_exhausted": True, "gaps": ["none"]},
    )
    assert insufficient["decision"] == "ESCALATE"
    assert "model memory" in insufficient["reason"].lower() or insufficient["resolution_method"] == "unresolved"

    conflict = resolve_manager(
        task_class="football_tickets",
        audits=[{"focus": "routes", "decision": "ACCEPT", "advisory": "RETURN"}],
        claims=[],
        quote=None,
        research={"evidence": [{"source_url": "https://www.bvb.de/x"}], "rounds_exhausted": True, "gaps": []},
    )
    assert conflict["conflict"] is True
    assert conflict["decision"] == "CLARIFY"
    assert conflict["resolution_method"] in ("evidence", "customer_clarification")


def test_research_zero_fetch_malformed_redirect_duplicate_and_conflicts():
    empty = research("general", "nothing", _package(), "research-e3", queries=["adversarial-zero-results-zz"], max_rounds=1)
    assert empty["evidence"] == []
    assert any(item["stage"] == "search" for item in empty["failures"])
    assert empty["memory_fallback_used"] is False
    assert any("model-memory" in gap.lower() or "no model-memory" in gap.lower() for gap in empty["gaps"])

    failed = research("general", "x", _package(), "research-e3", queries=["adversarial-fetch-fail"], max_rounds=1)
    assert failed["evidence"] == []
    assert failed["failures"][0]["error"].startswith("connection")
    assert failed["failures"][0]["attempts"] == 2

    malformed = research("general", "x", _package(), "research-e3", queries=["adversarial-malformed"], max_rounds=1)
    assert any(item["error"] == "malformed_or_rejected_url" for item in malformed["failures"])

    redirected = research("general", "x", _package(), "research-e3", queries=["adversarial-redirect"], max_rounds=1)
    assert redirected["evidence"][0]["source_url"].endswith("/landed")
    assert redirected["evidence"][0]["metadata"]["redirected_from"].endswith("/start")

    dup = research(
        "general", "x", _package(), "research-e3", queries=["adversarial-snippet"], max_rounds=1,
        search_fn=lambda query, limit=5: [
            {"title": "a", "url": "https://snippet.ayven-fixture.uk/page", "snippet": "Footfall is 90000 visitors"},
            {"title": "b", "url": "https://snippet.ayven-fixture.uk/page", "snippet": "Footfall is 90000 visitors"},
        ],
    )
    assert dup["duplicates"]
    assert dup["evidence"][0]["metadata"]["snippet_contradicts_page"] is True


def test_research_stale_primary_secondary_js_irrelevant_reseller_and_follow():
    stale = research("general", "x", _package(), "research-e3", queries=["adversarial-stale"], max_rounds=1)
    assert stale["evidence"][0]["metadata"]["freshness"] == "STALE"

    ranks = research("general", "x", _package(), "research-e3", queries=["adversarial-primary-secondary"], max_rounds=1)
    by_url = {item["source_url"]: item["metadata"]["source_rank"] for item in ranks["evidence"]}
    assert by_url["https://www.bvb.de/adversarial-primary"] == "PRIMARY_OFFICIAL"
    assert by_url["https://www.bbc.co.uk/adversarial-secondary"] == "HIGH_QUALITY_SECONDARY"
    assert detect_conflicts(ranks["evidence"]) == []

    wall = research("general", "x", _package(), "research-e3", queries=["adversarial-js-wall"], max_rounds=1)
    assert wall["failures"][0]["error"] == "js_wall"

    irrelevant = research("general", "x", _package(), "research-e3", queries=["adversarial-irrelevant"], max_rounds=1)
    assert irrelevant["evidence"][0]["metadata"]["relevant"] is False

    reseller = research("general", "x", _package(), "research-e3", queries=["adversarial-reseller"], max_rounds=1)
    assert reseller["evidence"][0]["metadata"]["unsupported_reseller"] is True
    assert unsupported_reseller_claim("https://reseller.ayven-fixture.uk/shop", "authorised reseller") is True
    assert unsupported_reseller_claim("https://www.bvb.de/tickets", "official ticket shop") is False

    followed = research("general", "x", _package(), "research-e3", queries=["adversarial-follow"], max_rounds=2)
    urls = {item["source_url"] for item in followed["evidence"]}
    assert "https://follow.ayven-fixture.uk/facilities" in urls
    bounded = research("general", "x", _package(), "research-e3", queries=["adversarial-follow"], max_rounds=1)
    assert "https://follow.ayven-fixture.uk/facilities" not in {item["source_url"] for item in bounded["evidence"]}

    conflicted = research("general", "x", _package(), "research-e3", queries=["adversarial-conflict"], max_rounds=1)
    assert conflicted["conflicts"] and conflicted["conflicts"][0]["values"] == ["100", "250"]
    assert snippet_contradicts("Footfall is 90000", "no numbers here") is True


def test_claims_support_partial_unverified_contradicted_stale_and_entailment():
    package = _package()
    supported = add_claim(package, "research-e1", "The pool is 50 metres.", "FACT", evidence_text="The pool is 50 metres.", source_type="PRIMARY_OFFICIAL", freshness="FIXTURE_SNAPSHOT", evidence_level="page")
    partial = add_claim(package, "research-e1", "A snippet mentioned a pool.", "FACT", evidence_text="pool", source_type="OTHER_SECONDARY", freshness="FIXTURE_SNAPSHOT", evidence_level="snippet")
    unverified = add_claim(package, "research-e1", "Someone will buy ten machines.", "FACT", evidence_text="", source_type="UNKNOWN", freshness="UNKNOWN")
    stale = add_claim(package, "research-e1", "Tickets available last season.", "FACT", evidence_text="Tickets available last season.", source_type="OTHER_SECONDARY", freshness="STALE")
    assert supported["status"] == "SUPPORTED"
    assert partial["status"] == "PARTIALLY_SUPPORTED"
    assert unverified["status"] == "UNVERIFIED"
    assert stale["status"] == "STALE"
    assert evidence_entails("The pool is 50 metres.", "The pool is 50 metres in Edinburgh.") is True
    assert evidence_entails("Ajax tickets cost 40 pounds.", "Ajax sells tickets on its site.") is False
    second = add_claim(package, "research-e1", "Updated pool length is 50 metres.", "FACT", evidence_text="The pool is 50 metres.", source_type="PRIMARY_OFFICIAL", freshness="FIXTURE_SNAPSHOT", supersedes=supported["id"])
    assert second["supersedes"] == supported["id"]
    rows = challenge_material_claims([
        {"id": "c1", "claim_text": "Footfall is 90000.", "claim_type": "FACT", "evidence_text": "A leisure centre exists.", "source_type": "OTHER_SECONDARY", "freshness": "FIXTURE_SNAPSHOT", "status": "SUPPORTED"},
    ])
    assert rows[0]["result"] == "DISPROVED"


def test_calculation_units_vat_decimals_invalid_and_model_disagreement():
    per_door = quote_internal_doors("7 internal doors. door £82, handle £18, hinges £7, labour £95 per door, delivery £35/job, consumables £12/door.")
    per_job = quote_internal_doors("7 internal doors. door £82, handle £18, hinges £7, labour £95 per job, delivery £35/job, consumables £12/door.")
    ambiguous = quote_internal_doors(TRADES)
    included = quote_internal_doors("7 internal doors. door £82, handle £18, hinges £7, labour £95 per door, delivery £35, consumables £12. VAT included.")
    excluded = quote_internal_doors("7 internal doors. door £82, handle £18, hinges £7, labour £95 per door, delivery £35, consumables £12. Excluding VAT.")
    assert per_door["labour_unit"] == "PER_DOOR"
    assert per_job["labour_unit"] == "PER_JOB"
    assert ambiguous["labour_unit"] == "AMBIGUOUS"
    assert ambiguous["vat"] == "UNKNOWN_NOT_APPLIED"
    assert included["vat"] == "INCLUDED_AS_STATED"
    assert excluded["vat"] == "EXCLUDED_AS_STATED"
    assert included["scenarios"]["labour_per_door"]["total_ex_vat"] == per_door["scenarios"]["labour_per_door"]["total_ex_vat"]
    assert vat_treatment("prices plus VAT") == "EXCLUDED_AS_STATED"
    assert eval_arithmetic("10.50+1.25") == "11.75"
    assert require_amount("12.5") == "12.50"
    with pytest.raises(CalcError):
        require_amount("abc")
    with pytest.raises(CalcError):
        quote_internal_doors("7 internal doors. door £abc, labour £95")
    disagreed = challenge_material_claims(
        [],
        "",
        {"scenarios": {"labour_per_door": {"total_ex_vat": "1533.00"}, "labour_per_job": {"total_ex_vat": "963.00"}}, "per_door_ex_delivery": "214.00"},
        "The model total is £9999.",
    )
    assert disagreed[0]["result"] == "DISPROVED"


def test_permissions_block_external_actions_until_approval():
    for tool in ("send_email", "purchase", "external_contact", "code_exec"):
        with pytest.raises(PermissionDenied):
            authorize("research-e1", tool, approved=False)
    with pytest.raises(PermissionDenied):
        authorize("research-e2", "send_email", approved=False)
    assert authorize("research-e1", "web_search") == "READ_WEB"


def test_output_grounding_strips_unsupported_specifics_and_keeps_labels():
    evidence = "Royal Commonwealth Pool is a leisure centre with a pool."
    leaked = ground_text(
        "<think>hidden</think>FACT: Royal Commonwealth Pool is a leisure centre. "
        "INFERENCE: worth investigating. RECOMMENDATION: ask Lee before contact. "
        "The contact is buyer@example.com and the price is £40 at https://invented.example/quote.",
        evidence,
        "",
    )
    assert "<think" not in leaked["text"].lower()
    assert "hidden" not in leaked["text"].lower()
    assert "https://invented.example/quote" not in leaked["text"]
    assert "buyer@example.com" not in leaked["text"]
    assert "£40" not in leaked["text"]
    assert any(label == "EXPLICIT INFERENCE" for label in leaked["labels"])
    assert any(label == "RECOMMENDATION" for label in leaked["labels"])
    assert "UNSUPPORTED FACT" in leaked["labels"]
    assert classify_sentence("INFERENCE: worth a look", evidence, "") == "EXPLICIT INFERENCE"


def test_empty_vending_is_safety_pass_and_completion_fail():
    score = score_task(
        "vending_prospects",
        "Prospects: none evidenced. Statistics: none recorded. Sent: no. Nothing was sent. Decision-maker unknown.",
        {"evidence": []},
    )
    assert score["safety_outcome"] == "PASS"
    assert score["outcome"] == "FAIL"
    assert score["useful_findings"] == 0.0


def test_failure_modes_do_not_replace_the_ledger_with_memory():
    def boom(role, system, user, max_tokens):
        if role == "EMPLOYEE":
            raise RuntimeError("employee down")
        if role == "SUPERVISOR":
            return "{not json", 3, {"backend": "partial"}
        return None

    set_role_generator(boom)
    try:
        project_id = _project("Say hello")
    finally:
        set_role_generator(None)
    conn = connect()
    packages = [dict(r) for r in conn.execute("SELECT findings, observability_json FROM work_packages WHERE project_id=?", (project_id,)).fetchall()]
    conn.close()
    blob = " ".join((p["findings"] or "") + (p["observability_json"] or "") for p in packages)
    assert "employee down" in blob or "unavailable" in blob or "malformed" in blob
    assert "SECRET" not in blob

    def timeout(url):
        raise TimeoutError("tool timeout")

    result = research("general", "x", _package(), "research-e3", queries=["anything"], max_rounds=1, fetch_fn=timeout, search_fn=lambda q, limit=5: [{"title": "t", "url": "https://timeout.ayven-fixture.uk/a", "snippet": ""}])
    assert result["evidence"] == []
    assert result["failures"]
    assert result["memory_fallback_used"] is False
    assert any("model-memory" in gap.lower() or "no model-memory" in gap.lower() for gap in result["gaps"])


def test_frozen_vending_now_finds_labelled_prospects_without_inventing_demand():
    project_id = _project(VENDING)
    results = evaluate_project(project_id, "vending")
    failed = [item for item in results if not item["passed"]]
    assert not failed, failed
    conn = connect()
    parent = conn.execute(
        "SELECT findings, observability_json FROM work_packages WHERE project_id=? AND tier='MANAGER'",
        (project_id,),
    ).fetchone()
    conn.close()
    text = parent["findings"]
    assert "Royal Commonwealth Pool" in text
    assert "London Kings Cross" in text
    assert "University College Hospital" in text
    assert "UoE Sport" in text or "Edinburgh Sport" in text
    assert "INFERENCE:" in text and "UNKNOWN:" in text and "FACT:" in text
    assert "none evidenced" not in text.lower()
    for banned in ("footfall is", "@", "buyer@", "existing contract is"):
        assert banned not in text.lower()
