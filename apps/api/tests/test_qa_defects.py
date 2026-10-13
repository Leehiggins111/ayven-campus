"""Regression tests for the four QA defects: restart, isolation, retries, acceptance run."""

import sys
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import connect
from app.distribution import update_package
from app.goals import once
from app.intelligence.clarification import blocking_question
from app.intelligence.execution import run_objective
from app.intelligence.recovery import resume_unfinished, run_bounded, run_retries, stage_attempts
from app.intelligence.store import insert_package, save_model_call
from app.main import app


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-10-13T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def _calls(package_id: str, role: str) -> int:
    conn = connect()
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM model_calls WHERE package_id=? AND role=?",
        (package_id, role),
    ).fetchone()["n"]
    conn.close()
    return count


def test_startup_resumes_a_killed_run_without_redoing_finished_work(monkeypatch):
    monkeypatch.setenv("AYVEN_RESUME", "1")
    monkeypatch.setenv("AYVEN_ENV", "local")
    monkeypatch.setenv("AYVEN_THREAD_STUB", "1")
    objective = "What is the sum of 12 and 30?"
    project = _project(objective)
    parent = insert_package(
        project_id=project,
        task_id="killed",
        title="Research programme",
        objective=objective,
        origin="milo",
        agent_id="research-mgr",
        tier="MANAGER",
        model_role="MANAGER",
        stage="employee",
        status="drafting",
    )
    update_package(parent, workflow_state="IN_PROGRESS")
    done = insert_package(
        project_id=project,
        task_id="killed",
        title="Deterministic calculation",
        objective=objective,
        origin="manager",
        agent_id="research-e1",
        parent_id=parent,
        tier="EMPLOYEE",
        model_role="EMPLOYEE",
        stage="employee",
        status="review",
    )
    update_package(done, findings="Calculator result 42.00.", workflow_state="COMPLETED")
    save_model_call(done, "EMPLOYEE", "calculation", {"completion_tokens": 1, "backend": "stub"}, "42.00")
    before = _calls(done, "EMPLOYEE")
    first = once(project, "research-notes", lambda: {"wrote": 1})
    assert first["replayed"] is False
    with TestClient(app):
        app.state.recovery_done.wait(20)
        conn = connect()
        status = conn.execute("SELECT status FROM projects WHERE id=?", (project,)).fetchone()["status"]
        state = conn.execute("SELECT workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone()["workflow_state"]
        parents = conn.execute(
            "SELECT COUNT(*) AS n FROM work_packages WHERE project_id=? AND parent_id IS NULL",
            (project,),
        ).fetchone()["n"]
        conn.close()
    assert _calls(done, "EMPLOYEE") == before
    replay = once(project, "research-notes", lambda: {"wrote": 2})
    assert replay["replayed"] is True
    assert replay["wrote"] == 1
    assert parents == 1
    assert status != "running"
    assert state != "IN_PROGRESS"


def test_resume_keeps_the_same_package_and_does_not_repeat_model_calls(monkeypatch):
    objective = "What is the sum of 12 and 30?"
    project = _project(objective)
    parent = run_objective(project, objective)
    before = _calls(parent, "MANAGER")
    conn = connect()
    conn.execute("UPDATE work_packages SET workflow_state=? WHERE id=?", ("IN_PROGRESS", parent))
    conn.execute("UPDATE projects SET status=? WHERE id=?", ("running", project))
    conn.commit()
    conn.close()
    monkeypatch.setenv("AYVEN_RESUME", "1")
    monkeypatch.setenv("AYVEN_ENV", "local")
    monkeypatch.setenv("AYVEN_THREAD_STUB", "1")
    resumed = resume_unfinished()
    assert resumed == [project]
    assert _calls(parent, "MANAGER") == before
    conn = connect()
    status = conn.execute("SELECT status FROM projects WHERE id=?", (project,)).fetchone()["status"]
    parents = conn.execute(
        "SELECT COUNT(*) AS n FROM work_packages WHERE project_id=? AND parent_id IS NULL",
        (project,),
    ).fetchone()["n"]
    conn.close()
    assert parents == 1
    assert status != "running"


def test_each_test_has_its_own_database(isolated_database):
    marker = f"isolation-{uuid.uuid4()}"
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        (marker, "isolation", "isolation", "running", "2026-10-13T00:00:00+00:00"),
    )
    conn.commit()
    stored = conn.execute("SELECT id FROM projects WHERE id=?", (marker,)).fetchone()["id"]
    conn.close()
    assert stored == marker
    assert isolated_database.name == "ayven.db"
    assert "/tmp/ayven-campus.db" not in str(isolated_database)
    assert "/tmp/ayven-pytest-intel.db" not in str(isolated_database)


def test_a_sibling_test_does_not_see_the_other_database():
    conn = connect()
    rows = conn.execute("SELECT id FROM projects WHERE title='isolation'").fetchall()
    conn.close()
    assert rows == []


def test_a_timed_out_worker_cannot_write_later():
    def slow():
        time.sleep(0.45)
        conn = connect()
        conn.execute(
            "INSERT INTO memories(id, namespace, content, created_at) VALUES(?,?,?,?)",
            ("late-write", "orphan", "should-not-land", "2026-10-13T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()

    before = len(stage_attempts())
    started = time.monotonic()
    value, err = run_bounded("research", slow, timeout=0.05)
    elapsed = time.monotonic() - started
    time.sleep(0.7)
    assert value is None
    assert err == "research_timeout"
    assert elapsed < 0.35
    assert len(stage_attempts()) == before + 1
    conn = connect()
    row = conn.execute("SELECT id FROM memories WHERE id='late-write'").fetchone()
    conn.close()
    assert row is None


def test_retries_use_backoff_then_escalate_to_lee(monkeypatch):
    monkeypatch.setenv("AYVEN_RETRY_BACKOFF_S", "0.02")
    monkeypatch.setenv("AYVEN_MAX_ATTEMPTS", "3")
    project = _project("A step that always fails.")
    continued: list[str] = []

    def fail(_attempt: int) -> dict:
        return {"ok": False, "error": "model returned 500"}

    result = run_retries(
        fail,
        stage="qa",
        max_attempts=3,
        project_id=project,
        task_id="qa-task",
        independent=lambda: continued.append("independent-task"),
    )
    assert result["ok"] is False
    assert result["external_sends"] == 0
    assert [row["attempt"] for row in result["attempts"]] == [1, 2, 3]
    assert result["attempts"][0]["backoff_s"] == 0
    assert result["attempts"][2]["backoff_s"] > result["attempts"][1]["backoff_s"] > 0
    assert result["attempts"][0]["at"] < result["attempts"][1]["at"] < result["attempts"][2]["at"]
    assert result["blocker"]["kind"] == "error"
    assert "director" in result["blocker"]["reason"].lower()
    # The exact cause is the blocker. A failed step never advances to approval.
    assert "model returned 500" in result["blocker"]["reason"]
    assert result["approval"] is None
    assert continued == ["independent-task"]
    conn = connect()
    approvals = conn.execute("SELECT COUNT(*) AS n FROM approvals WHERE project_id=?", (project,)).fetchone()["n"]
    conn.close()
    assert approvals == 0


def test_a_failed_delivery_records_the_exact_blocker():
    from app.intelligence.execution import _delivery_blocker

    reason = _delivery_blocker(
        {"passed": False, "reason": "Invalid review", "missing_items": ["Three prospects"]},
        ["OperationalError: database is locked"],
    )
    assert "OperationalError: database is locked" in reason
    assert "Invalid review" in reason
    assert "Three prospects" in reason
    assert _delivery_blocker({"passed": False}, []) == "Required deliverable did not pass its checks"


def test_acceptance_brief_runs_end_to_end_simulated(tmp_path, monkeypatch):
    monkeypatch.setenv("AYVEN_LLM_STUB", "1")
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from validation.wf1 import ACCEPTANCE_OBJECTIVE, run

    assert blocking_question(ACCEPTANCE_OBJECTIVE) == ""
    assert blocking_question("Research it") != ""
    report = run(tmp_path / "qa", "reg-wf1")
    assert report["run_type"] == "SIMULATED"
    assert report["label"] == "SIMULATED"
    assert report["stalled"] is False
    assert report["engine_state"] != "AWAITING_CLARIFICATION"
    assert report["verdict"] == "PASS"
    assert report["scores"]["total"] >= 80
    assert (tmp_path / "qa" / "reg-wf1" / "verdict.json").is_file()
    assert (tmp_path / "qa" / "reg-wf1" / "verdict.md").is_file()
