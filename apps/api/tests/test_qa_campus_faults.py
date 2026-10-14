"""QA campus fault re-check (14 Oct 2026): bundle files, criteria scoring, blockers, budget, grounding."""

import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.intelligence.store import insert_package
from app.main import app
from tests.test_web_artifact import built  # noqa: F401  (module-scoped browser build fixture)


def _project(objective: str, status: str = "running") -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, status, "2026-10-14T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def _bundle(package_id: str) -> list[str]:
    response = TestClient(app).get(f"/work-packages/{package_id}/bundle.zip")
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    return sorted(archive.namelist())


# (a) Program and web deliverables land in bundle.zip -----------------------------------------

def test_a_tested_program_lands_in_bundle_zip_end_to_end(monkeypatch):
    from app.intelligence.execution import resume_approved_package
    from app.intelligence.workflow import log_transition
    from app.orchestrator import finalize_project
    from tests.test_software_build import _PROGRAM, _run

    parent = _run(monkeypatch, _PROGRAM, "Write a small Python program that adds 2 and 3 and prints the sum.")
    conn = connect()
    project_id = conn.execute("SELECT project_id FROM work_packages WHERE id=?", (parent,)).fetchone()["project_id"]
    conn.close()
    finalize_project(project_id)
    assert _bundle(parent) == ["main.py", "report.md", "test_main.py"]
    response = TestClient(app).get(f"/work-packages/{parent}/bundle.zip")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "return left + right" in archive.read("main.py").decode()
    log_transition(parent, "APPROVED", "test approval")
    assert resume_approved_package(parent) == "COMPLETED"
    finalize_project(project_id)
    assert _bundle(parent) == ["main.py", "report.md", "test_main.py"]


def test_a_failed_program_puts_no_source_in_bundle_zip(monkeypatch):
    from app.orchestrator import finalize_project
    from tests.test_software_build import _BROKEN, _run

    parent = _run(monkeypatch, _BROKEN, "Write a small Python program that adds 2 and 3 and prints the sum.")
    conn = connect()
    project_id = conn.execute("SELECT project_id FROM work_packages WHERE id=?", (parent,)).fetchone()["project_id"]
    conn.close()
    finalize_project(project_id)
    response = TestClient(app).get(f"/work-packages/{parent}/bundle.zip")
    if response.status_code == 200:
        assert not any(n.endswith(".py") for n in zipfile.ZipFile(io.BytesIO(response.content)).namelist())


def test_a_verified_web_app_lands_in_bundle_zip_end_to_end(monkeypatch, built):
    import app.intelligence.web_artifact as module
    from app.intelligence.execution import run_objective
    from app.models import set_role_generator
    from app.orchestrator import finalize_project
    from tests.test_web_artifact import FILES

    (report, meta), _calls = built
    assert meta["tests_passed"] is True, meta.get("web_execution")
    monkeypatch.setattr(module, "build_web_artifact", lambda *_a: (report, meta))
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    objective = "Build a customer job tracking application with create, edit, persistence and validation."
    project_id = _project(objective)
    set_role_generator(lambda role, s, u, m: ("SYNTHESISE\nNothing was sent." if role == "MANAGER" else "ACCEPT\nNothing was sent.", 2, {"backend": "generator"}))
    try:
        parent = run_objective(project_id, objective)
    finally:
        set_role_generator(None)
    finalize_project(project_id)
    names = _bundle(parent)
    assert "report.md" in names
    assert set(FILES) <= set(names), names


# (a) Grounding: the £25 price -------------------------------------------------------------

def test_a_price_lee_supplied_in_the_acceptance_criteria_is_not_dropped():
    from app.goals import open_goal
    from app.intelligence.execution import Programme

    project_id = _project("Write a dog grooming price sheet for a small salon. Publish nothing.")
    open_goal(project_id, ["Full groom price: £25", "Mentions the cancellation policy"])
    from app.intelligence.grounding import ground_text

    programme = Programme.__new__(Programme)
    programme.project_id = project_id
    programme.objective = "Write a dog grooming price sheet for a small salon. Publish nothing."
    programme.research = {"evidence": []}
    programme.quote = None
    evidence, deterministic = programme._corpus()
    grounded = ground_text("Full groom price: £25 per dog.", evidence, deterministic)
    assert grounded["removed"] == []
    assert "£25" in grounded["text"]


def test_a_price_nobody_supplied_is_still_dropped():
    from app.intelligence.grounding import ground_text

    grounded = ground_text("Full groom price: £25 per dog.", "", "Write a dog grooming price sheet. Publish nothing.")
    assert grounded["removed"] == ["Full groom price: £25 per dog."]


def test_money_is_matched_as_a_whole_figure():
    from app.intelligence.grounding import ground_text

    # £250 in the brief does not back £25 (the old substring check let it through).
    assert ground_text("Full groom price: £25 per dog.", "", "Full groom £250")["removed"]
    # £25.00 and £25 are the same amount.
    assert not ground_text("Full groom price: £25.00 per dog.", "", "Full groom £25")["removed"]
    assert not ground_text("Full groom price: £25 per dog.", "", "Full groom £25.00")["removed"]
    assert not ground_text("Supply is £1,200 for the lot.", "", "Budget 1200 pounds")["removed"]


# (b) Criteria scoring ---------------------------------------------------------------------

def test_instruction_verbs_are_not_required_terms():
    from app.goals import _criterion_terms

    assert _criterion_terms("Mentions the cancellation policy") == ["cancellation", "policy"]
    assert _criterion_terms("Covers payment terms") == ["payment", "terms"]
    assert _criterion_terms("Includes a price for a full groom") == ["price", "full", "groom"]
    assert _criterion_terms("Describes the 24 hour notice") == ["24", "hour", "notice"]


def test_delivered_content_is_met_without_echoing_the_instruction_verb():
    from app.goals import evaluate_criteria, open_goal

    project_id = _project("Write a dog grooming price sheet.")
    open_goal(project_id, ["Mentions the cancellation policy", "Covers payment terms", "Includes a price for a full groom"])
    report = "Full groom price: £25.\nCancellation policy: 24 hours notice.\nPayment terms: pay by card."
    assert {r["criterion"]: r["status"] for r in evaluate_criteria(project_id, report)} == {
        "Mentions the cancellation policy": "met",
        "Covers payment terms": "met",
        "Includes a price for a full groom": "met",
    }


def test_a_job_that_fails_at_planning_reports_not_met_for_each_criterion():
    from app.durable import _fail_project
    from app.goals import open_goal
    from app.milo_handoff import job_status

    project_id = _project("Write a dog grooming price sheet.")
    open_goal(project_id, ["Mentions the cancellation policy", "Covers payment terms"])
    conn = connect()
    conn.execute("CREATE TABLE IF NOT EXISTS durable_checkpoints (job_id TEXT NOT NULL, stage TEXT NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY(job_id,stage))")
    conn.commit()
    conn.close()
    _fail_project(project_id, "prepare", "ValueError: Ayven could not produce a valid job plan")
    results = job_status(project_id)["criteria_results"]
    assert results == [
        {"criterion": "Mentions the cancellation policy", "status": "not_met"},
        {"criterion": "Covers payment terms", "status": "not_met"},
    ]


def test_a_rejected_job_never_scored_still_lists_each_criterion_as_not_met():
    from app.goals import criteria_for, open_goal

    project_id = _project("Write a dog grooming price sheet.")
    open_goal(project_id, ["Covers payment terms"])
    conn = connect()
    # The failure reason mentions the criterion's words; it is not a deliverable.
    conn.execute("UPDATE projects SET status='rejected', result=? WHERE id=?", ("Stopped: payment terms planning failed", project_id))
    conn.commit()
    conn.close()
    assert criteria_for(project_id) == [{"criterion": "Covers payment terms", "status": "not_met"}]


# (c) Blocker written once -----------------------------------------------------------------

def test_the_same_review_blocker_is_written_once():
    from app.goals import add_blocker, list_blockers
    from app.intelligence.recovery import run_retries

    project_id = _project("Review twice.")
    parent = insert_package(
        project_id=project_id, task_id="b", title="Research programme", objective="Review twice.",
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="running",
    )
    for _ in range(2):  # the review and the post-repair re-review
        run_retries(lambda _n: {"ok": False, "error": "malformed_review"}, stage="delivery-review",
                    max_attempts=1, project_id=project_id, task_id=parent)
    blockers = [b for b in list_blockers(project_id=project_id) if "delivery-review" in b["reason"]]
    assert len(blockers) == 1
    first = add_blocker(project_id, "error", "different reason", "Lee", package_id=parent)
    assert first["reason"] == "different reason"
    assert len(list_blockers(project_id=project_id)) == 2


# (d) Budget cap on the status endpoint --------------------------------------------------

def test_the_status_endpoint_shows_the_budget_cap():
    from app.goals import open_goal

    project_id = _project("Budget visible.")
    open_goal(project_id, ["Covers payment terms"], job_cap_gbp=1.23)
    body = TestClient(app).get(f"/milo/jobs/{project_id}").json()
    assert body["job_cap_gbp"] == 1.23
    assert body["job_cap_usd"] == pytest.approx(1.6246, abs=0.01)
    assert "spent_gbp" in body
