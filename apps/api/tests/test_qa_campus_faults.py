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


# (a) The real submitted-job path: HTTP -> durable runner -> builder -> approval -> bundle.zip --------

_HTTP_PROGRAM_JOB = r'''
import io, json, time, zipfile
from fastapi.testclient import TestClient
from app.main import app
from app.models import set_role_generator

PROGRAM = """FILE: main.py
def add(left, right):
    return left + right

FILE: test_main.py
import main
assert main.add(4, 7) == 11
assert main.add(0, 0) == 0

LAUNCH: python test_main.py
"""
seen = []

def gen(role, system, user, max_tokens):
    seen.append((role, system[:60]))
    if role == "MANAGER":
        return "SYNTHESISE\nRationale: the files are the deliverable", 3, {"backend": "mock"}
    if role == "SUPERVISOR":
        return "ACCEPT\nNothing was sent.", 2, {"backend": "mock"}
    if "Write the test file only" in user or "Write main.py only" in user:
        return PROGRAM, 40, {"backend": "mock"}
    if "example_output" in user:
        return json.dumps({"function": "add", "parameters": ["left", "right"], "example_input": [4, 7], "example_output": 11}), 8, {"backend": "mock"}
    return "Nothing was sent.", 2, {"backend": "mock"}

set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
with TestClient(app) as client:
    r = client.post("/milo/jobs", json={"objective": "Write a small Python program that adds 4 and 7 and prints the sum.",
                                        "acceptance_criteria": ["Adds 4 and 7"]}, headers=auth)
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    for _ in range(300):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    assert s["status"] == "waiting", s
    assert s["workflow_state"] == "AWAITING_APPROVAL", s
    assert any("specification" in sys_ for _, sys_ in seen), seen  # the software builder ran
    pkg = s["package_id"]
    names = sorted(zipfile.ZipFile(io.BytesIO(client.get(f"/work-packages/{pkg}/bundle.zip", headers=auth).content)).namelist())
    assert names == ["main.py", "report.md", "test_main.py"], names
    approval = s["approval"]
    assert approval, s
    r = client.post(f"/approvals/{approval['id']}/resolve", json={"decision": "approved", "reason": ""}, headers=auth)
    assert r.status_code == 200, r.text
    for _ in range(300):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] == "complete":
            break
        time.sleep(0.1)
    assert s["status"] == "complete", s
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/work-packages/{pkg}/bundle.zip", headers=auth).content))
    assert sorted(z.namelist()) == ["main.py", "report.md", "test_main.py"]
    assert "return left + right" in z.read("main.py").decode()
print("OK")
'''


def test_a_program_job_submitted_over_http_puts_its_py_files_in_bundle_zip(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    api = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(api), "AYVEN_DURABLE": "1", "AYVEN_DB": str(tmp_path / "http.sqlite"),
           "AYVEN_LLM_STUB": "0", "AYVEN_LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1", "AYVEN_AGENT_RUNTIME": "native",
           "AYVEN_USE_QWEN_AGENT": "0", "AYVEN_ALLOW_CODE": "1", "AYVEN_RESEARCH_MODE": "fixtures",
           "AYVEN_ALLOW_ESCALATION": "0", "AYVEN_API_TOKEN": "private-api-key-with-at-least-32-characters"}
    env.pop("AYVEN_WORKFLOW_DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", _HTTP_PROGRAM_JOB], cwd=str(api), env=env, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0 and "OK" in result.stdout, (result.stdout + result.stderr)[-4000:]


_HTTP_WEB_JOB = r'''
import io, json, time, zipfile
from fastapi.testclient import TestClient
from app.main import app
from app.models import set_role_generator
from tests.test_web_artifact import FILES, PLAN

seen = []

def gen(role, system, user, max_tokens):
    seen.append(system[:40])
    if role == "MANAGER":
        return "SYNTHESISE\nNothing was sent.", 2, {"backend": "mock"}
    if role == "SUPERVISOR":
        return "ACCEPT\nNothing was sent.", 2, {"backend": "mock"}
    if system.startswith("Specify"):
        return json.dumps(PLAN), 30, {"backend": "mock"}
    if system.startswith("Independently"):
        return json.dumps({"passed": True, "missing": []}), 5, {"backend": "mock"}
    for name in FILES:
        if system.startswith("Write " + name + " only"):
            return json.dumps({name: FILES[name]}), 30, {"backend": "mock"}
    return "Nothing was sent.", 2, {"backend": "mock"}

set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
with TestClient(app) as client:
    r = client.post("/milo/jobs", json={"objective": "Build a customer job tracking application with create, edit, persistence and validation."}, headers=auth)
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    for _ in range(1200):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    assert s["workflow_state"] == "AWAITING_APPROVAL", (s, seen)
    assert s["web_artifact_verified"] is True, s
    names = set(zipfile.ZipFile(io.BytesIO(client.get(f"/work-packages/{s['package_id']}/bundle.zip", headers=auth).content)).namelist())
    assert set(FILES) | {"report.md"} <= names, names
print("OK")
'''


def test_a_web_job_submitted_over_http_puts_its_files_in_bundle_zip(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from app.intelligence.web_browser_runner import chrome_path

    if not chrome_path():
        pytest.skip("Playwright Chromium is not installed")
    api = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(api), "AYVEN_DURABLE": "1", "AYVEN_DB": str(tmp_path / "web.sqlite"),
           "AYVEN_LLM_STUB": "0", "AYVEN_LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1", "AYVEN_AGENT_RUNTIME": "native",
           "AYVEN_USE_QWEN_AGENT": "0", "AYVEN_ALLOW_CODE": "1", "AYVEN_RESEARCH_MODE": "fixtures",
           "AYVEN_ALLOW_ESCALATION": "0", "AYVEN_API_TOKEN": "private-api-key-with-at-least-32-characters"}
    env.pop("AYVEN_WORKFLOW_DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", _HTTP_WEB_JOB], cwd=str(api), env=env, capture_output=True, text=True, timeout=400)
    assert result.returncode == 0 and "OK" in result.stdout, (result.stdout + result.stderr)[-4000:]


def test_a_review_failed_job_marks_its_criteria_not_met():
    from app.goals import criteria_for, open_goal
    from app.intelligence.store import update_package as store_update
    from app.orchestrator import finalize_project

    project_id = _project("Write a dog grooming price sheet.")
    open_goal(project_id, ["Mentions the cancellation policy", "Covers payment terms"])
    parent = insert_package(
        project_id=project_id, task_id="rf", title="Research programme", objective="Write a dog grooming price sheet.",
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="complete",
    )
    # The draft would meet both criteria, but the delivery review rejected it.
    store_update(parent, findings="Cancellation policy: 24 hours notice.\nPayment terms: pay by card.", workflow_state="FAILED")
    finalize_project(project_id)
    assert criteria_for(project_id) == [
        {"criterion": "Mentions the cancellation policy", "status": "not_met"},
        {"criterion": "Covers payment terms", "status": "not_met"},
    ]
