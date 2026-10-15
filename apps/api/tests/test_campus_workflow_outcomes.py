"""Campus workflow outcomes (15 Oct 2026): what Lee may do with a result.

Blocked, failed, rejected, simulated and completed results are told apart in one
place (app/outcome.py) and every download, the Campus view and Milo's status use it.
"""

import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.goals import add_blocker, criteria_evidence, criteria_for, evaluate_criteria, get_goal, open_goal
from app.intelligence.store import insert_package, update_package
from app.main import app
from app.outcome import outcome_for

PROGRAM_FINDINGS = """# Software deliverable
Tests: PASSED
Sandbox: bwrap
Launch: python test_main.py

## main.py
def add(left, right):
    return left + right

## test_main.py
import main
assert main.add(4, 7) == 11

Test output:
passed

Nothing was sent.
"""


def _project(mode: str = "SIMULATED", status: str = "running") -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at,run_mode) VALUES(?,?,?,?,?,?)",
        (project_id, "Job", "Write a program.", status, "2026-10-15T00:00:00+00:00", mode),
    )
    conn.commit()
    conn.close()
    return project_id


def _package(project_id: str, state: str, findings: str = "Draft about Edinburgh desks. Nothing was sent.", error: str = "") -> str:
    package_id = insert_package(
        project_id=project_id, task_id="t-" + project_id, title="Research programme", objective="Write a program.",
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="x",
    )
    update_package(package_id, workflow_state=state, findings=findings, error=error)
    return package_id


def _approval(project_id: str, status: str, reason: str = "", accepted_unmet: int = 0) -> str:
    from app.goals import ensure_schema

    ensure_schema()
    approval_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at,reason,accepted_unmet) VALUES(?,?,?,?,?,?,?,?,?)",
        (approval_id, "t-" + project_id, project_id, "research-mgr", "Review", status, "2026-10-15T00:00:00+00:00", reason, accepted_unmet),
    )
    conn.commit()
    conn.close()
    return approval_id


# Outcome states -------------------------------------------------------------------------------

def test_a_simulated_completed_result_is_labelled_simulated():
    package = _package(_project("SIMULATED"), "COMPLETED")
    outcome = outcome_for(package_id=package)
    assert outcome["state"] == "COMPLETED" and outcome["accepted_deliverable"] is True
    assert outcome["simulated"] is True and outcome["label"] == "COMPLETED (SIMULATED)"
    assert outcome["evidence"] == "SIMULATED"


def test_a_live_configuration_is_unverified_not_proven():
    outcome = outcome_for(package_id=_package(_project("UNVERIFIED"), "COMPLETED"))
    assert outcome["state"] == "COMPLETED" and outcome["simulated"] is False
    assert outcome["evidence"] == "LIVE_UNVERIFIED"


def test_rejected_by_lee_and_failed_step_are_different_outcomes():
    rejected = _project()
    _package(rejected, "FAILED")
    _approval(rejected, "rejected", "Name no price.")
    failed = _project()
    _package(failed, "FAILED", error="Build failed: the program's tests did not pass.")
    r, f = outcome_for(project_id=rejected), outcome_for(project_id=failed)
    assert r["state"] == "REJECTED" and "Name no price." in r["reason"]
    assert f["state"] == "FAILED" and "Build failed" in f["reason"]
    for outcome in (r, f):
        assert outcome["diagnostic_only"] is True and outcome["accepted_deliverable"] is False


def test_completed_with_an_unmet_criterion_is_not_an_accepted_deliverable():
    project = _project()
    open_goal(project, ["Adds 4 and 7"])
    package = _package(project, "COMPLETED", PROGRAM_FINDINGS)
    evaluate_criteria(project, PROGRAM_FINDINGS)
    outcome = outcome_for(package_id=package)
    assert outcome["state"] == "BLOCKED" and outcome["accepted_deliverable"] is False
    assert "not met" in outcome["reason"]


def test_an_explicit_override_is_completed_and_says_so():
    project = _project()
    open_goal(project, ["Adds 4 and 7"])
    package = _package(project, "COMPLETED", PROGRAM_FINDINGS)
    evaluate_criteria(project, PROGRAM_FINDINGS)
    _approval(project, "approved", "Tests prove the sum.", accepted_unmet=1)
    outcome = outcome_for(package_id=package)
    assert outcome["state"] == "COMPLETED" and outcome["criteria_overridden"] is True
    assert "UNMET CRITERIA ACCEPTED BY LEE" in outcome["label"] and "Tests prove the sum." in outcome["reason"]


def test_blockers_unresolved_clarification_and_draft_states():
    capped = _project()
    package = _package(capped, "IN_PROGRESS")
    add_blocker(capped, "cap", "Paused: this job has used its budget.", "Raise the cap.", package)
    assert outcome_for(package_id=package)["state"] == "BLOCKED"
    assert outcome_for(package_id=_package(_project(), "UNRESOLVED"))["state"] == "BLOCKED"
    assert outcome_for(package_id=_package(_project(), "AWAITING_CLARIFICATION"))["state"] == "NEEDS_CLARIFICATION"
    draft = outcome_for(package_id=_package(_project(), "AWAITING_APPROVAL"))
    assert draft["state"] == "NEEDS_APPROVAL" and draft["draft_for_review"] and not draft["accepted_deliverable"]
    assert outcome_for(project_id="no-such-job")["state"] == "UNKNOWN"


# Acceptance checks carry evidence ----------------------------------------------------------------

def test_each_criterion_records_the_terms_found_and_missing():
    project = _project()
    open_goal(project, ["Adds 4 and 7", "Quotes £25 per visit"])
    evaluate_criteria(project, PROGRAM_FINDINGS)
    assert criteria_for(project) == [
        {"criterion": "Adds 4 and 7", "status": "not_met"},
        {"criterion": "Quotes £25 per visit", "status": "not_met"},
    ]
    first, second = criteria_evidence(project)
    assert first["evaluated"] is True and first["evidence"]["found"] == ["4", "7"] and first["evidence"]["missing"] == ["adds"]
    assert len(first["evidence"]["report_sha256"]) == 64 and first["evidence"]["report_chars"] == len(PROGRAM_FINDINGS)
    assert second["evidence"]["note"] == "A figure is missing."


def test_nothing_delivered_is_recorded_as_not_met_with_a_reason():
    project = _project()
    open_goal(project, ["Adds 4 and 7"])
    evaluate_criteria(project, "")
    row = criteria_evidence(project)[0]
    assert row["status"] == "not_met" and "Nothing was delivered" in row["evidence"]["note"]


# Downloads ------------------------------------------------------------------------------------------

def test_a_rejected_draft_downloads_only_as_a_labelled_diagnostic():
    project = _project()
    package = _package(project, "FAILED", PROGRAM_FINDINGS)
    _approval(project, "rejected", "Wrong function name.")
    client = TestClient(app)
    response = client.get(f"/work-packages/{package}/deliverable.md")
    assert response.status_code == 200
    assert response.text.startswith("> REJECTED. NOT A DELIVERABLE.")
    assert "Wrong function name." in response.text.split("\n\n")[0]
    assert "diagnostic" in response.headers["content-disposition"] and "REJECTED" in response.headers["content-disposition"]
    assert response.headers["x-ayven-outcome"] == "REJECTED"
    assert client.get(f"/work-packages/{package}/files/main.py").status_code == 409
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/work-packages/{package}/bundle.zip").content))
    assert archive.namelist() == ["report.md"]
    assert archive.read("report.md").decode().startswith("# REJECTED. NOT A DELIVERABLE.")


def test_a_draft_downloads_with_a_draft_banner_and_a_completed_simulated_one_says_simulated():
    client = TestClient(app)
    draft = _package(_project(), "AWAITING_APPROVAL", PROGRAM_FINDINGS)
    shown = client.get(f"/work-packages/{draft}/deliverable.md")
    assert shown.text.startswith("> DRAFT FOR REVIEW. Not accepted.") and "return left + right" in shown.text
    assert client.get(f"/work-packages/{draft}/files/main.py").status_code == 200
    done = _package(_project("SIMULATED"), "COMPLETED", PROGRAM_FINDINGS)
    shown = client.get(f"/work-packages/{done}/deliverable.md")
    assert shown.text.startswith("> SIMULATED.") and shown.headers["x-ayven-evidence"] == "SIMULATED"
    live = _package(_project("UNVERIFIED"), "COMPLETED", PROGRAM_FINDINGS)
    assert client.get(f"/work-packages/{live}/deliverable.md").text == PROGRAM_FINDINGS


def test_files_from_an_unfinished_job_are_not_served():
    package = _package(_project(), "IN_PROGRESS", PROGRAM_FINDINGS)
    assert TestClient(app).get(f"/work-packages/{package}/files/main.py").status_code == 409


# Approval gate ------------------------------------------------------------------------------------------

def test_approving_with_an_unmet_criterion_needs_an_explicit_reasoned_override():
    project = _project()
    open_goal(project, ["Adds 4 and 7"])
    _package(project, "AWAITING_APPROVAL", PROGRAM_FINDINGS)
    evaluate_criteria(project, PROGRAM_FINDINGS)
    approval = _approval(project, "pending")
    client = TestClient(app)
    refused = client.post(f"/approvals/{approval}/resolve", json={"decision": "approved"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["unmet_criteria"][0]["criterion"] == "Adds 4 and 7"
    no_reason = client.post(f"/approvals/{approval}/resolve", json={"decision": "approved", "accept_unmet_criteria": True})
    assert no_reason.status_code == 400
    conn = connect()
    status = conn.execute("SELECT status FROM approvals WHERE id=?", (approval,)).fetchone()["status"]
    conn.close()
    assert status == "pending"  # neither refusal decided anything


def test_rework_does_not_promise_work_that_never_starts():
    package = _package(_project(), "AWAITING_APPROVAL")
    body = TestClient(app).post(f"/work-packages/{package}/rework", json={"reason": "Use the north office."}).json()
    assert body["rework_started"] is False
    assert "does not start automatically" in body["needed_from_lee"]


# Budgets keep their currency and amount ---------------------------------------------------------------

def test_a_dollar_budget_stays_in_dollars(monkeypatch):
    monkeypatch.setenv("AYVEN_JOB_BUDGET_USD", "10.00")
    goal = open_goal("usd-job", [], budget_usd=1.50)
    assert goal["job_cap_currency"] == "USD" and goal["job_cap_amount"] == 1.5
    assert goal["job_cap_gbp_is_conversion"] is True and goal["job_cap_source"] == "sent"
    pound = open_goal("gbp-job-2", [], job_cap_gbp=6.25)
    assert (pound["job_cap_currency"], pound["job_cap_amount"], pound["job_cap_gbp"]) == ("GBP", 6.25, 6.25)
    assert pound["job_cap_gbp_is_conversion"] is False
    assert get_goal("usd-job")["job_cap_amount"] == 1.5


def test_the_monthly_budget_keeps_the_pounds_lee_set():
    client = TestClient(app)
    for amount in (33.33, 50.0, 0.07):
        view = client.put("/budget", json={"monthly_cap_gbp": amount}).json()
        assert view["monthly_cap_gbp"] == amount and view["monthly_cap_currency"] == "GBP"
        assert client.get("/budget").json()["monthly_cap_gbp"] == amount
    assert client.put("/budget", json={"monthly_cap_gbp": 0}).status_code == 400


def test_a_job_the_month_cannot_afford_is_refused_before_it_exists(monkeypatch):
    from app.intelligence.budget import check_and_reserve, commit

    monkeypatch.setenv("AYVEN_MONTHLY_BUDGET_USD", "1.00")
    monkeypatch.setenv("AYVEN_BUDGET_ENFORCE", "1")
    reserved = check_and_reserve("spent-job", "EMPLOYEE", 20, model_id="local")
    commit(reserved["reservation_id"], input_tokens=20, output_tokens=4, amount_usd=1.00)
    conn = connect()
    before = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    conn.close()
    response = TestClient(app).post("/milo/jobs", json={"objective": "Write a program.", "job_cap_gbp": 1.0})
    assert response.status_code == 400 and "was not created" in response.json()["detail"]
    conn = connect()
    after = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    conn.close()
    assert after == before  # no orphan job is left behind


def test_an_unsupported_budget_is_refused_not_clamped(monkeypatch):
    monkeypatch.setenv("AYVEN_JOB_BUDGET_USD", "2.00")
    response = TestClient(app).post("/projects", json={"objective": "Write a program.", "job_cap_gbp": 500})
    assert response.status_code == 400 and "£500.00" in response.json()["detail"]


# Status surfaces ------------------------------------------------------------------------------------------

def test_milo_status_and_campus_view_carry_the_outcome():
    from app.milo_handoff import job_status

    project = _project()
    package = _package(project, "FAILED", "Draft about Edinburgh desks.")
    _approval(project, "rejected", "Name no price.")
    status = job_status(project)
    assert status["outcome"]["state"] == "REJECTED"
    assert status["result"].startswith("REJECTED. NOT A DELIVERABLE.") and "Edinburgh" in status["result"]
    view = TestClient(app).get("/campus/view", params={"package_id": package}).json()
    assert view["outcome"]["state"] == "REJECTED" and view["outcome"]["diagnostic_only"] is True


def test_health_reports_where_the_workflow_engine_keeps_its_state(monkeypatch):
    monkeypatch.delenv("AYVEN_WORKFLOW_DATABASE_URL", raising=False)
    body = TestClient(app).get("/health").json()
    assert body["workflow_store"] == "instance_local_sqlite" and body["workflow_store_durable"] is False
    assert body["durability_caveats"]


def test_the_campus_page_has_the_workflow_controls():
    page = TestClient(app).get("/r3f/campus-app.jsx").text
    for testid in ("budget-amount", "budget-currency", "criteria-input", "brief-error", "outcome-label",
                   "simulated-badge", "accept-unmet", "decision-error", "download-diagnostic", "clarify-error"):
        assert f'data-testid="{testid}"' in page or f"\"{testid}\"" in page, testid
