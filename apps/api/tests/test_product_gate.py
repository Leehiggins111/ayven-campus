"""Product gates added after the login and fixture-approval commits."""

import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import connect, db_path
from app.intelligence.clarification import blocking_question
from app.intelligence.execution import run_objective
from app.main import app
from app.orchestrator import resolve_approval


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-10-09T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def test_vague_requests_ask_and_named_work_proceeds():
    assert blocking_question("Create a business launch plan.")
    assert blocking_question("Research it")
    assert blocking_question("help me")
    assert not blocking_question("Create a business launch plan for Alba Kitchen Refresh.")
    assert not blocking_question("Research the public market for office supplies and name the gaps.")
    assert not blocking_question("What is the sum of 12 and 30?")
    assert not blocking_question("Calculate 6 * 7")


def test_vague_request_stays_on_the_same_package():
    objective = "Create a business launch plan."
    project = _project(objective)
    parent = run_objective(project, objective, "task-vague")
    conn = connect()
    row = dict(conn.execute("SELECT * FROM work_packages WHERE id=?", (parent,)).fetchone())
    conn.close()
    assert row["workflow_state"] == "AWAITING_CLARIFICATION"
    assert row["clarification_question"]
    client = TestClient(app)
    resumed = client.post(
        f"/work-packages/{parent}/clarification",
        json={"answer": "Alba Kitchen Refresh, a local kitchen painting service"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["package_id"] == parent
    conn = connect()
    parents = conn.execute(
        "SELECT id FROM work_packages WHERE project_id=? AND parent_id IS NULL",
        (project,),
    ).fetchall()
    conn.close()
    assert [item["id"] for item in parents] == [parent]


def test_calculate_six_times_seven_is_forty_two():
    objective = "Calculate 6 * 7"
    project = _project(objective)
    parent = run_objective(project, objective, "task-six-seven")
    conn = connect()
    row = dict(conn.execute("SELECT findings, workflow_state, task_class FROM work_packages WHERE id=?", (parent,)).fetchone())
    approval = conn.execute("SELECT id FROM approvals WHERE task_id=?", ("task-six-seven",)).fetchone()
    conn.close()
    assert row["task_class"] == "calculation"
    assert "42.00" in (row["findings"] or "")
    if row["workflow_state"] == "AWAITING_APPROVAL":
        assert approval
        client = TestClient(app)
        resolved = client.post(f"/approvals/{approval['id']}/resolve", json={"decision": "approved"})
        assert resolved.status_code == 200
        conn = connect()
        after = conn.execute("SELECT workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone()
        conn.close()
        assert after["workflow_state"] == "COMPLETED"
    else:
        assert row["workflow_state"] == "COMPLETED"


def test_direct_resolve_stays_approved():
    objective = "Research the public market for office supplies and name the gaps."
    project = _project(objective)
    parent = run_objective(project, objective, "task-direct-resolve")
    conn = connect()
    approval = conn.execute("SELECT id FROM approvals WHERE task_id=?", ("task-direct-resolve",)).fetchone()
    conn.close()
    resolve_approval(approval["id"], "approved")
    conn = connect()
    state = conn.execute("SELECT workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone()["workflow_state"]
    conn.close()
    assert state == "APPROVED"


def test_access_gate_blocks_data_and_keeps_health_open(monkeypatch):
    monkeypatch.setenv("AYVEN_ACCESS_KEY", "night-gate-secret")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    blocked = client.get("/state")
    assert blocked.status_code == 401
    denied = client.post("/login", json={"key": "wrong"})
    assert denied.status_code == 401
    signed = client.post("/login", json={"key": "night-gate-secret"})
    assert signed.status_code == 200
    assert client.get("/state").status_code == 200
    bare = TestClient(app)
    assert bare.get("/state").status_code == 401


def test_backup_copies_the_database_and_leaves_the_source():
    source = Path(db_path())
    before = source.read_bytes()
    body = TestClient(app).get("/admin/backup").json()
    assert source.read_bytes() == before
    assert Path(body["path"]) != source
    assert body["sha256"]
    assert body["durable"] is False
    assert Path(body["path"]).read_bytes() == before


def test_access_stays_off_when_the_key_is_unset(monkeypatch):
    monkeypatch.delenv("AYVEN_ACCESS_KEY", raising=False)
    os.environ.pop("AYVEN_ACCESS_KEY", None)
    health = TestClient(app).get("/health").json()
    assert health["access_protected"] is False
