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


# Failed builds never reach approval; accept and resolve agree -------------------------------

def _http_env(tmp_path, name: str, **extra) -> dict:
    import os
    from pathlib import Path

    api = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(api), "AYVEN_DURABLE": "1", "AYVEN_DB": str(tmp_path / f"{name}.sqlite"),
           "AYVEN_LLM_STUB": "0", "AYVEN_LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1", "AYVEN_AGENT_RUNTIME": "native",
           "AYVEN_USE_QWEN_AGENT": "0", "AYVEN_ALLOW_CODE": "1", "AYVEN_RESEARCH_MODE": "fixtures",
           "AYVEN_ALLOW_ESCALATION": "0", "AYVEN_API_TOKEN": "private-api-key-with-at-least-32-characters", **extra}
    env.pop("AYVEN_WORKFLOW_DATABASE_URL", None)
    return env


def _run_script(script: str, env: dict, timeout: int = 300) -> None:
    import subprocess
    import sys
    from pathlib import Path

    api = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", script], cwd=str(api), env=env, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0 and "OK" in result.stdout, (result.stdout + result.stderr)[-4000:]


_FAILED_JOB_CHECKS = r'''
def failed_job_checks(client, auth, pid, criteria):
    import io, time, zipfile
    for _ in range(1200):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    assert s["status"] == "rejected", s
    assert s["workflow_state"] == "FAILED", s
    assert s["approval"] is None, s
    from app.db import connect
    conn = connect()
    approvals = conn.execute("SELECT status FROM approvals WHERE project_id=?", (pid,)).fetchall()
    blockers = [r["reason"] for r in conn.execute("SELECT reason FROM blockers WHERE project_id=? AND status='open'", (pid,)).fetchall()]
    conn.close()
    assert [dict(a) for a in approvals] == [], approvals
    assert any(b.startswith("Build failed") for b in blockers), blockers
    assert s["criteria_results"] == [{"criterion": c, "status": "not_met"} for c in criteria], s["criteria_results"]
    z = client.get(f"/work-packages/{s['package_id']}/bundle.zip", headers=auth)
    assert z.status_code == 200, z.text
    archive = zipfile.ZipFile(io.BytesIO(z.content))
    assert archive.namelist() == ["report.md"], archive.namelist()
    report = archive.read("report.md").decode()
    assert report.startswith("# FAILED"), report[:300]
    assert report.index("Build failed") < report.index("## Checks"), report[:800]
    assert "## Report" not in report and "```" not in report and "~~~" not in report
    accept = client.post(f"/work-packages/{s['package_id']}/accept", headers=auth)
    assert accept.status_code == 409, accept.text
    return s
'''


def test_a_failing_program_build_over_http_is_failed_with_a_blocker_not_approval(tmp_path):
    script = _HTTP_PROGRAM_JOB.split("set_role_generator(gen)")[0].replace("return left + right", "return left") + _FAILED_JOB_CHECKS + r'''
set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
criteria = ["Adds 4 and 7"]
with TestClient(app) as client:
    r = client.post("/milo/jobs", json={"objective": "Write a small Python program that adds 4 and 7 and prints the sum.", "acceptance_criteria": criteria}, headers=auth)
    assert r.status_code == 200, r.text
    s = failed_job_checks(client, auth, r.json()["project_id"], criteria)
    assert "Tests: FAILED" in s["result"]
print("OK")
'''
    _run_script(script, _http_env(tmp_path, "badprog"))


def test_a_web_build_whose_browser_cannot_launch_is_failed_and_not_met(tmp_path):
    script = _HTTP_WEB_JOB.split("set_role_generator(gen)")[0] + _FAILED_JOB_CHECKS + r'''
set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
criteria = ["Customer job tracking application"]
with TestClient(app) as client:
    r = client.post("/milo/jobs", json={"objective": "Build a customer job tracking application with create, edit, persistence and validation.", "acceptance_criteria": criteria}, headers=auth)
    assert r.status_code == 200, r.text
    s = failed_job_checks(client, auth, r.json()["project_id"], criteria)
    assert s["web_artifact_verified"] is False
    # The FAILED web report carries no app source: no fixture content, no fenced html/css/js, no artifact blob.
    import io, re, zipfile
    z = client.get(f"/work-packages/{s['package_id']}/bundle.zip", headers=auth)
    assert z.status_code == 200, z.text
    archive = zipfile.ZipFile(io.BytesIO(z.content))
    assert archive.namelist() == ["report.md"], archive.namelist()
    report = archive.read("report.md").decode()
    for name, source in FILES.items():
        assert source not in report, name
        for line in [ln for ln in source.splitlines() if len(ln.strip()) > 20][:5]:
            assert line not in report, (name, line)
    assert not re.search(r"^```(?:[A-Za-z0-9_./-]+\.(?:html?|css|js)|ayven-artifact|html|css|js|javascript)\b", report, re.M), report
    assert "localStorage" not in report and "<!doctype" not in report.lower()
    # Failure narrative is kept.
    assert report.startswith("# FAILED") and "Build failed" in report and "Tests: FAILED" in report, report[:800]
print("OK")
'''
    # Simulated launch failure: the browser executable does not exist.
    _run_script(script, _http_env(tmp_path, "nobrowser", AYVEN_CHROME_PATH="/nonexistent/chrome"))


def test_accept_resolves_the_pending_approval_like_resolve_does(tmp_path):
    script = _HTTP_PROGRAM_JOB.split("set_role_generator(gen)")[0] + r'''
set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
with TestClient(app) as client:
    r = client.post("/milo/jobs", json={"objective": "Write a small Python program that adds 4 and 7 and prints the sum."}, headers=auth)
    pid = r.json()["project_id"]
    for _ in range(300):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    assert s["workflow_state"] == "AWAITING_APPROVAL", s
    approval_id = s["approval"]["id"]
    accept = client.post(f"/work-packages/{s['package_id']}/accept", headers=auth)
    assert accept.status_code == 200, accept.text
    body = accept.json()
    assert body["approval_id"] == approval_id and body["workflow_state"] == "COMPLETED" and body["status"] == "completed", body
    s = client.get("/milo/jobs/" + pid, headers=auth).json()
    assert s["status"] == "complete" and s["workflow_state"] == "COMPLETED", s
    from app.db import connect
    conn = connect()
    status = conn.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()["status"]
    conn.close()
    assert status == "approved", status
    # Resolving the same approval again (the other path) changes nothing, and accepting again is a no-op.
    client.post(f"/approvals/{approval_id}/resolve", json={"decision": "approved"}, headers=auth)
    assert client.get("/milo/jobs/" + pid, headers=auth).json()["workflow_state"] == "COMPLETED"
    again = client.post(f"/work-packages/{s['package_id']}/accept", headers=auth)
    assert again.status_code == 200 and again.json()["workflow_state"] == "COMPLETED", again.text
print("OK")
'''
    _run_script(script, _http_env(tmp_path, "accept"))



# Audit trail: refused or repeated decisions write nothing; accept twice is a no-op; failed zip --------

_AUDIT_JOB = _HTTP_PROGRAM_JOB.split("set_role_generator(gen)")[0] + r"""
set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
from app.db import connect

def audit(pid, aid):
    conn = connect()
    reason = conn.execute("SELECT reason, status FROM approvals WHERE id=?", (aid,)).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM spend_ledger WHERE job_id=? AND kind='intervention'", (pid,)).fetchone()[0]
    pkg = conn.execute("SELECT workflow_state FROM work_packages WHERE project_id=? AND parent_id IS NULL", (pid,)).fetchone()[0]
    goal = conn.execute("SELECT status, updated_at FROM goal_contracts WHERE project_id=?", (pid,)).fetchone()
    project = conn.execute("SELECT status FROM projects WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    return (reason["reason"], reason["status"], count, pkg, tuple(goal) if goal else None, project)

with TestClient(app) as client:
    r = client.post("/milo/jobs", json={"objective": "Write a small Python program that adds 4 and 7 and prints the sum.", "acceptance_criteria": ["Adds 4 and 7"]}, headers=auth)
    pid = r.json()["project_id"]
    for _ in range(300):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    assert s["workflow_state"] == "AWAITING_APPROVAL", s
    aid = s["approval"]["id"]
    ok = client.post(f"/approvals/{aid}/resolve", json={"decision": "approved", "reason": "Looks right"}, headers=auth)
    assert ok.status_code == 200, ok.text
    before = audit(pid, aid)
    assert before[0] == "Looks right" and before[1] == "approved" and before[3] == "COMPLETED", before

    # Refused flip: 409 and nothing written.
    flip = client.post(f"/approvals/{aid}/resolve", json={"decision": "rejected", "reason": "Changed my mind"}, headers=auth)
    assert flip.status_code == 409, flip.text
    assert audit(pid, aid) == before, (audit(pid, aid), before)

    # Repeat approve with a different reason: no-op, original reason kept.
    again = client.post(f"/approvals/{aid}/resolve", json={"decision": "approved", "reason": "second note"}, headers=auth)
    assert again.status_code == 200 and again.json().get("unchanged") is True, again.text
    assert audit(pid, aid) == before, (audit(pid, aid), before)

    # Accept twice on the completed package: true no-op both times.
    for _ in range(2):
        acc = client.post(f"/work-packages/{s['package_id']}/accept", headers=auth)
        assert acc.status_code == 200 and acc.json()["workflow_state"] == "COMPLETED", acc.text
        assert audit(pid, aid) == before, (audit(pid, aid), before)
print("OK")
"""


def test_refused_or_repeated_decisions_and_double_accept_write_nothing(tmp_path):
    _run_script(_AUDIT_JOB, _http_env(tmp_path, "audit"))


def test_accept_twice_logs_one_intervention(tmp_path):
    script = _HTTP_PROGRAM_JOB.split("set_role_generator(gen)")[0] + r"""
set_role_generator(gen)
auth = {"Authorization": "Bearer private-api-key-with-at-least-32-characters"}
from app.db import connect
with TestClient(app) as client:
    pid = client.post("/milo/jobs", json={"objective": "Write a small Python program that adds 4 and 7 and prints the sum."}, headers=auth).json()["project_id"]
    for _ in range(300):
        s = client.get("/milo/jobs/" + pid, headers=auth).json()
        if s["status"] not in ("running", "queued"):
            break
        time.sleep(0.1)
    for _ in range(2):
        assert client.post(f"/work-packages/{s['package_id']}/accept", headers=auth).status_code == 200
    conn = connect()
    rows = conn.execute("SELECT reason FROM spend_ledger WHERE job_id=? AND kind='intervention'", (pid,)).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["Lee accepted the result"], rows
print("OK")
"""
    _run_script(script, _http_env(tmp_path, "accept2"))


def test_a_failed_package_zip_has_only_the_report_with_the_reason_first():
    from app.blobs import save_bytes
    from app.goals import add_blocker
    from app.intelligence.store import update_package as store_update
    from app.intelligence.workflow import log_transition

    project_id = _project("Write a program.")
    parent = insert_package(
        project_id=project_id, task_id="fz", title="Research programme", objective="Write a program.",
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="failed",
    )
    store_update(parent, findings="# Software deliverable\nTests: FAILED\nRejected: AssertionError\nNothing was sent.\n",
                 error="Build failed: the program's tests did not pass. AssertionError")
    log_transition(parent, "FAILED", "build failed")
    add_blocker(project_id, "error", "Build failed: the program's tests did not pass. AssertionError", "Fix it", package_id=parent)
    save_bytes(parent, "main.py", b"def add(a, b):\n    return a\n", "program")  # a stray stored source must never ship
    response = TestClient(app).get(f"/work-packages/{parent}/bundle.zip")
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist() == ["report.md"]
    report = archive.read("report.md").decode()
    assert report.startswith("# FAILED")
    assert report.index("## Failure reason") < report.index("## Blockers") < report.index("## Checks")
    assert "Tests: FAILED" in report and "return a" not in report and "Rejected: AssertionError" not in report


def test_a_failed_package_without_a_report_is_404():
    from app.intelligence.workflow import log_transition

    project_id = _project("Nothing.")
    parent = insert_package(
        project_id=project_id, task_id="fz2", title="Research programme", objective="Nothing.",
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="failed",
    )
    log_transition(parent, "FAILED", "planning failed")
    assert TestClient(app).get(f"/work-packages/{parent}/bundle.zip").status_code == 404



# FAILED report.md is built from structured fields only: every QA injection variant (r119) -----------

def _fixture_markers():
    from tests.test_web_artifact import FILES

    markers = ["<!doctype", "localStorage", "let jobs", "```", "~~~", "ayven-artifact", "def add(", "return left",
               "assert main.add", "import main", "body{font", "document.querySelector"]
    for source in FILES.values():
        markers += [line.strip()[:40] for line in source.splitlines() if len(line.strip()) > 20][:5]
    return markers


def _variants():
    import json as _json
    from tests.test_web_artifact import FILES

    html, css, js = FILES["index.html"], FILES["style.css"], FILES["app.js"]
    prog = "def add(left, right):\n    return left\n"
    test = "import main\nassert main.add(4, 7) == 11\n"
    head = "# Browser application deliverable\nTests: FAILED\n"
    return {
        "unfenced web source": head + html + "\n" + js + "\n",
        "indented code block": head + "\n".join("    " + line for line in (html + "\n" + css).splitlines()) + "\n",
        "~~~ fence": head + "~~~html\n" + html + "\n~~~\n",
        "``` fence no language": head + "```\n" + js + "\n```\n",
        "```HTML uppercase tag": head + "```HTML\n" + html + "\n```\n",
        "```index.HTML uppercase filename": head + "```index.HTML\n" + html + "\n```\n",
        "```javascript tag": head + "```javascript\n" + js + "\n```\n",
        "inline ayven-artifact JSON unfenced": head + "ayven-artifact " + _json.dumps(FILES) + "\n",
        "unfenced python (program report)": "# Software deliverable\nTests: FAILED\n" + prog + test,
        "```python tag (program report)": "# Software deliverable\nTests: FAILED\n```python\n" + prog + "```\n```python\n" + test + "```\n",
    }


def _failed_zip_report(findings: str, blocker: str = "Build failed: the browser check did not pass (unavailable).",
                       error: str = "Build failed: the browser check did not pass (unavailable).", task_class: str = "application_build") -> str:
    from app.goals import add_blocker
    from app.intelligence.store import update_package as store_update
    from app.intelligence.workflow import log_transition

    project_id = _project("Build a customer job tracking application.")
    parent = insert_package(
        project_id=project_id, task_id="v-" + uuid.uuid4().hex[:6], title="Research programme", objective="Build a customer job tracking application.",
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="failed",
    )
    store_update(parent, findings=findings, error=error, task_class=task_class)
    log_transition(parent, "FAILED", "build failed")
    add_blocker(project_id, "error", blocker, "Fix it", package_id=parent)
    response = TestClient(app).get(f"/work-packages/{parent}/bundle.zip")
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist() == ["report.md"]
    return archive.read("report.md").decode()


@pytest.mark.parametrize("name", list(_variants()))
def test_no_injected_source_variant_reaches_the_failed_report(name):
    findings = _variants()[name]
    task = "software_build" if "program" in name else "application_build"
    report = _failed_zip_report(findings, task_class=task)
    leaks = [m for m in _fixture_markers() if m.lower() in report.lower()]
    assert not leaks, (name, leaks, report)
    assert not any(line.startswith(("    ", "\t")) for line in report.splitlines()), report
    assert report.startswith("# FAILED") and "## Failure reason" in report and "## Blockers" in report
    assert "- Tests: FAILED" in report


def test_source_quoted_inside_a_blocker_or_reason_is_collapsed():
    from tests.test_web_artifact import FILES

    quoted = "Review: invalid output\n```html\n" + FILES["index.html"] + "\n```\n" + FILES["app.js"]
    report = _failed_zip_report("Tests: FAILED\n", blocker=quoted, error="Build failed:\n    " + FILES["app.js"])
    leaks = [m for m in _fixture_markers() if m.lower() in report.lower()]
    assert not leaks, (leaks, report)
    assert "Review: invalid output [code omitted]" in report, report
    assert "[code omitted]" in report.split("## Failure reason")[1].split("##")[0]


def test_failed_report_lines_are_truncated():
    report = _failed_zip_report("Tests: FAILED\n", blocker="Review: " + "x" * 2000, error="Build failed: " + "y" * 2000)
    assert all(len(line) <= 310 for line in report.splitlines()), max(len(line) for line in report.splitlines())


def test_failed_report_keeps_the_job_brief_and_status_but_never_the_report_body():
    report = _failed_zip_report("Tests: FAILED\nSome narrative the model wrote about the app.\n")
    assert "Brief: Build a customer job tracking application." in report
    assert "- Tests: FAILED" in report and "Browser check: failed" in report
    assert "Some narrative" not in report


# Sanitiser hardening (QA retest d8d6127): blobs, Python blocks, inline-only replacement, fallback ----

def _qa_d8d_variants():
    import base64
    import html as _html
    import json as _json
    import re as _re
    from tests.test_web_artifact import FILES

    HTML, JS = FILES["index.html"], FILES["app.js"]
    PY = "def add(left, right):\n    return left + right\n\nif __name__ == '__main__':\n    print(add(4, 7))\n"
    TPY = "import main\ndef test_add():\n    assert main.add(4, 7) == 11\n"
    ART = _json.dumps({"files": FILES})
    MIN = _re.sub(r"\s*\n\s*", " ", JS)
    SPLIT = "The build failed while checking the page. " + ("x" * 250) + " " + JS
    SPLIT2 = "Step one passed and step two passed and the form saved a record and the list rendered fine. " * 3 + "\nlet jobs=[]\ntry{jobs=JSON.parse(localStorage.getItem('jobs')||'[]')}catch(e){}"
    B64 = base64.b64encode(HTML.encode()).decode()
    ENT = _html.escape(HTML)

    def fence(tag, src):
        return f"```{tag}\n{src}\n```"

    return [
        ("unfenced web source", "W", "findings", HTML + "\n" + JS),
        ("indented code block", "W", "findings", "Report\n\n" + "\n".join("    " + l for l in HTML.splitlines())),
        ("~~~ fence", "W", "findings", "~~~html\n" + HTML + "\n~~~"),
        ("``` no language", "W", "findings", fence("", JS)),
        ("```HTML uppercase", "W", "findings", fence("HTML", HTML)),
        ("```index.HTML", "W", "findings", fence("index.HTML", HTML)),
        ("```javascript", "W", "findings", fence("javascript", JS)),
        ("unfenced ayven-artifact JSON", "W", "findings", "ayven-artifact " + ART),
        ("unfenced python (program)", "P", "findings", PY + "\n" + TPY),
        ("```python (program)", "P", "findings", fence("python", PY)),
        ("source quoted in blocker", "W", "blocker", "Build failed: the page is:\n" + fence("html", HTML)),
        ("source in job title", "W", "title", HTML),
        ("source in job brief", "W", "objective", "Build this exactly:\n" + HTML + "\n" + JS),
        ("python in brief (program)", "P", "objective", "Write this program: " + PY),
        ("lone __main__ line in brief", "P", "objective", "Run it with\nif __name__ == '__main__':"),
        ("one-line minified JS in error", "W", "error", MIN),
        ("one-line minified JS in blocker", "W", "blocker", MIN),
        ("minified JS in title", "W", "title", MIN),
        ("source past 300 cap (error)", "W", "error", SPLIT),
        ("prose then code lines (blocker)", "W", "blocker", SPLIT2),
        ("HTML entities in error", "W", "error", "Rendered page: " + ENT),
        ("HTML entities in brief", "W", "objective", "Use this page " + _html.escape("<script>localStorage.setItem('a','b')</script>")),
        ("base64 in error", "W", "error", "Artifact payload: " + B64),
        ("base64 in blocker", "W", "blocker", "Bundle " + base64.b64encode(PY.encode()).decode()),
        ("base64 html-escaped in blocker", "W", "blocker", "Bundle " + _html.escape(base64.b64encode(HTML.encode()).decode())),
        ("source in browser error", "W", "weberr", "Error: page threw\n" + JS),
        ("one-line source in browser error", "W", "weberr", MIN),
        ("entities in browser error", "W", "weberr", "Error: " + ENT.replace("\n", " ")),
        ("python call no colon in error", "P", "error", "Build failed: print(add(4, 7)) gave 12; expected sum 11 with add(left, right) return left + right"),
    ], FILES, PY, TPY


def _qa_scan(text: str, fragments: set) -> list:
    import base64
    import html as _html
    import re as _re

    hits = []
    views = {"raw": text, "unescaped": _html.unescape(text)}
    for m in _re.findall(r"[A-Za-z0-9+/]{16,}={0,2}", text):
        for pad in ("", "=", "=="):
            try:
                views["b64"] = views.get("b64", "") + base64.b64decode(m[: len(m) - len(m) % 4] + pad).decode("utf8", "ignore")
                break
            except Exception:
                continue
    for name, view in views.items():
        found = [f for f in fragments if f in view]
        if found:
            hits.append(f"{name}: fragment {found[0]!r}")
        for marker in ["```", "~~~", "<!doctype", "<script", "localStorage", "ayven-artifact", "def add", "return left",
                       "assert main.add", "JSON.parse", "=>", "__main__"]:
            if marker.lower() in view.lower():
                hits.append(f"{name}: marker {marker!r}")
    return sorted(set(hits))


def _inject_failed(which: str, field: str, value: str) -> str:
    import json as _json
    from app.goals import add_blocker
    from app.intelligence.store import update_package as store_update
    from app.intelligence.workflow import log_transition

    task = "software_build" if which == "P" else "application_build"
    objective = "Write a small Python program that adds 4 and 7." if which == "P" else "Build a customer job tracking application."
    project_id = _project(objective)
    parent = insert_package(
        project_id=project_id, task_id="d8-" + uuid.uuid4().hex[:6], title="Research programme", objective=objective,
        origin="milo", agent_id="research-mgr", tier="MANAGER", model_role="MANAGER", stage="results", status="failed",
    )
    obs = {"software_execution": {"passed": False, "exit_code": 1}} if which == "P" else {"web_execution": {"passed": False, "status": "unavailable", "error": "Executable doesn't exist"}}
    fields = {"findings": "Tests: FAILED\n", "error": "Build failed: the checks did not pass.", "task_class": task}
    blocker = "Build failed: the checks did not pass."
    if field in ("findings", "error"):
        fields[field] = value
    elif field == "blocker":
        blocker = value
    elif field == "weberr":
        obs["web_execution"]["error"] = value
    elif field in ("title", "objective"):
        conn = connect()
        conn.execute(f"UPDATE projects SET {field}=? WHERE id=?", (value, project_id))
        conn.commit()
        conn.close()
        fields[field] = value
    fields["observability_json"] = _json.dumps(obs)
    store_update(parent, **fields)
    log_transition(parent, "FAILED", "build failed")
    add_blocker(project_id, "error", blocker, "Fix it", package_id=parent)
    response = TestClient(app).get(f"/work-packages/{parent}/bundle.zip")
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist() == ["report.md"]
    return archive.read("report.md").decode()


def test_every_qa_d8d_variant_is_clean_over_http():
    import re as _re

    variants, files, py, tpy = _qa_d8d_variants()
    fragments = set()
    for src in list(files.values()) + [py, tpy]:
        for line in src.splitlines():
            line = line.strip()
            for i in range(0, max(1, len(line) - 15), 8):
                frag = line[i:i + 16]
                if len(frag) == 16 and _re.search(r"[(){};=<>\[\]]", frag):
                    fragments.add(frag)
    leaks = {}
    for name, which, field, value in variants:
        report = _inject_failed(which, field, value)
        assert report.startswith("# FAILED") and "Nothing was sent." in report, name
        hits = _qa_scan(report, fragments)
        if hits:
            leaks[name] = (hits, report)
    assert not leaks, leaks


def test_base64_is_replaced_in_full_and_when_it_would_be_truncated():
    import base64
    from app.main import _safe_line
    from tests.test_web_artifact import FILES

    blob = base64.b64encode(FILES["index.html"].encode()).decode()
    assert _safe_line("Payload: " + blob) == "Payload: [data omitted]"
    # Truncation happens after the blob is replaced, so no decodable prefix survives a cut.
    out = _safe_line("Payload: " + blob, limit=120)
    assert "[data omitted]" in out and blob[:40] not in out
    out = _safe_line(("word " * 50) + blob, limit=300)
    assert blob[:20] not in out
    # Split across lines, each piece still 40+ characters.
    assert blob[:60] not in _safe_line("Payload:\n" + blob[:60] + "\n" + blob[60:120])


def test_a_lone_main_guard_and_python_block_lines_are_dropped():
    from app.main import _safe_line

    assert "__main__" not in _safe_line("if __name__ == '__main__':")
    for line in ("if x > 3:", "for item in items:", "while True:", "class Thing:", "def add(a, b):"):
        out = _safe_line("Context words\n" + line)
        assert out == "Context words [code omitted]", (line, out)


_QA_REALISTIC_BLOCKERS = [
    "Build failed: the program's tests did not pass. AssertionError",
    "Build failed: the browser check did not pass (Error: BrowserType.launch: Failed to launch chromium because executable doesn't exist at /nonexistent/chrome).",
    "The delivery review did not return valid JSON, so the work could not be checked.",
    "Budget cap reached: spent £1.48 of £1.50; stopping before the next step.",
    "Criterion not met: Full groom price is £25 (report says £30).",
    "Test failed: expected 11, got 12.",
    "AssertionError: assert 12 == 11",
    "Timeout after 120s waiting for the model.",
    "Model returned HTTP 429 (rate limited); retry later.",
    "The page has no <form> element, so jobs cannot be created.",
    "Clicking Save did nothing: the list stayed empty after reload.",
    "Validation missing: an empty customer name was accepted.",
    "Edit button not found; selector #edit-1 matched nothing.",
    "Error: page.click: Timeout 30000ms exceeded.",
    "TypeError: Cannot read properties of null (reading 'value')",
    "Network error: connect ECONNREFUSED 127.0.0.1:8080",
    "Needs Lee: confirm the price list before we publish anything.",
    "The brief asks for x = 5 items per page but the page shows 10.",
    "Exit code 1 from pytest; 2 tests failed, 1 passed.",
    "Missing file: index.html was not produced.",
    "Price must be at least £10; the sheet lists a £5 nail trim.",
    "Reason: the customer list is not saved between visits {persistence}.",
    "Step 3 of 5 failed; see the browser check.",
    "ModuleNotFoundError: No module named 'main'",
    "Form submits but the new job is not shown; expected 1 row, found 0.",
]

# Only the code span itself may change; the rest must read as written.
_EXPECTED_CHANGES = {
    "The page has no <form> element, so jobs cannot be created.": "The page has no [code omitted] element, so jobs cannot be created.",
    "The brief asks for x = 5 items per page but the page shows 10.": "The brief asks for [code omitted] items per page but the page shows 10.",
}


@pytest.mark.parametrize("message", _QA_REALISTIC_BLOCKERS)
def test_realistic_blockers_keep_their_meaningful_text(message):
    from app.main import _safe_line

    assert _safe_line(message) == _EXPECTED_CHANGES.get(message, message)


def test_inline_code_is_replaced_in_place_and_the_sentence_survives():
    from app.main import _safe_line

    assert _safe_line("Build failed: `print(add(4, 7))` gave 12, expected 11.") == "Build failed: [code omitted] gave 12, expected 11."
    assert _safe_line("Build failed: print(add(4, 7)) gave 12, expected 11.") == "Build failed: [code omitted] gave 12, expected 11."
    assert _safe_line("The delivery review did not return valid JSON.") == "The delivery review did not return valid JSON."


def test_an_empty_cleaned_reason_falls_back_to_what_failed():
    from app.main import _safe_line

    assert _safe_line("```python\ndef add(a, b):\n    return a\n```") == "[code omitted]"
    program = _inject_failed("P", "error", "```python\ndef add(a, b):\n    return a\n```")
    assert program.split("## Failure reason")[1].split("##")[0].strip() == "Build failed: tests did not pass."
    web = _inject_failed("W", "error", "<!doctype html><html><body></body></html>")
    assert web.split("## Failure reason")[1].split("##")[0].strip() == "Build failed: browser check did not pass."
    empty = _inject_failed("W", "error", "")
    assert "Build failed: browser check did not pass." in empty


# Chunked encodings and the long-token exemption (QA retest 690a949) -----------------------------

def _b64_chunks(text: str, size: int = 30) -> str:
    import base64

    blob = base64.b64encode(text.encode()).decode()
    return " ".join(blob[i:i + size] for i in range(0, len(blob), size))


def _hex_chunks(text: str, size: int = 32) -> str:
    import binascii

    blob = binascii.hexlify(text.encode()).decode()
    return " ".join(blob[i:i + size] for i in range(0, len(blob), size))


def _no_decodable_source(report: str) -> None:
    import base64
    import binascii
    import re as _re

    for marker in ("<!doctype", "def add", "return left", "__main__"):
        assert marker not in report.lower()
    joined = "".join(_re.findall(r"[A-Za-z0-9+/=]{8,}", report))
    for start in range(4):
        try:
            decoded = base64.b64decode(joined[start:][: (len(joined) - start) // 4 * 4]).decode("utf8", "ignore")
        except Exception:
            decoded = ""
        assert "doctype" not in decoded and "def add" not in decoded
    hexes = "".join(_re.findall(r"[0-9a-fA-F]{8,}", report))
    try:
        assert b"def add" not in binascii.unhexlify(hexes[: len(hexes) // 2 * 2])
    except binascii.Error:
        pass


_PROGRAM_SOURCE = "def add(left, right):\n    return left + right\n\nif __name__ == '__main__':\n    print(add(4, 7))\n"


def test_chunked_base64_in_the_reason_is_omitted():
    from tests.test_web_artifact import FILES

    report = _inject_failed("W", "error", "Payload: " + _b64_chunks(FILES["index.html"]))
    assert "Payload: [data omitted]" in report
    _no_decodable_source(report)


def test_chunked_base64_in_a_blocker_split_across_lines_is_omitted():
    chunks = _b64_chunks(_PROGRAM_SOURCE, 20).split(" ")
    value = "Bundle " + " ".join(chunks[:3]) + "\n" + " ".join(chunks[3:])
    report = _inject_failed("P", "blocker", value)
    assert "- Bundle [data omitted]" in report
    _no_decodable_source(report)


def test_hex_in_32_char_chunks_is_omitted():
    report = _inject_failed("P", "blocker", "Bundle " + _hex_chunks(_PROGRAM_SOURCE))
    assert "- Bundle [data omitted]" in report
    _no_decodable_source(report)


def test_chunk_rule_runs_after_truncation_too():
    from app.main import _safe_line

    out = _safe_line(("word " * 55) + _b64_chunks(_PROGRAM_SOURCE, 12), limit=300)
    assert len(out) <= 300
    _no_decodable_source(out)


def test_prose_words_never_look_like_chunks():
    from app.main import _safe_line

    for text in ("Necessary validation requirements incomplete for Database configuration.",
                 "Customer information persisted correctly between sessions afterwards.",
                 "deadline12 is not data on its own"):
        assert _safe_line(text) == text


def test_urls_and_paths_are_kept():
    from app.main import _safe_line

    url = "https://docs.example.com/guides/browser-checks/troubleshooting-launch-failures.html"
    assert _safe_line(f"See {url} for help.") == f"See {url} for help."
    path = "/home/box/.cache/ms-playwright/chromium-1243/chrome-linux/headless_shell.log"
    assert _safe_line(f"Error: executable doesn't exist at {path}.") == f"Error: executable doesn't exist at {path}."
    query = "https://example.com/search?q=browser+launch&page=2"
    assert _safe_line(f"Read {query} first") == f"Read {query} first"


def test_a_url_hiding_base64_in_its_query_or_fragment_or_path_loses_only_that_part():
    import base64
    from app.main import _safe_line
    from tests.test_web_artifact import FILES

    blob = base64.b64encode(FILES["index.html"].encode()).decode()[:150]
    assert _safe_line(f"Link https://example.com/x?data={blob} done") == "Link https://example.com/x?[data omitted] done"
    assert _safe_line(f"Link https://example.com/x#{blob} done") == "Link https://example.com/x#[data omitted] done"
    assert _safe_line(f"Link https://example.com/{blob[:60]}/page.html done") == "Link https://example.com/[data omitted]/page.html done"
    assert blob[:20] not in _safe_line(f"https://example.com/?a={blob[:40]}&b=1")


def test_qa_realistic_blockers_unchanged_except_the_two_accepted():
    from app.main import _safe_line

    changed = {m: _safe_line(m) for m in _QA_REALISTIC_BLOCKERS if _safe_line(m) != m}
    assert changed == _EXPECTED_CHANGES, changed
