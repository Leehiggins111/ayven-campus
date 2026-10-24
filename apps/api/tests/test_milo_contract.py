"""Milo's job contract: constraints, mode, and idempotency."""

from fastapi.testclient import TestClient

from app.goals import list_handovers, list_tasks
from app.intelligence.execution import Programme, _manager_user, _supervisor_user
from app.main import app


def _client(monkeypatch):
    from app import orchestrator

    monkeypatch.setattr(orchestrator, "run_project", lambda project_id: None)
    return TestClient(app)


def test_constraints_reach_the_director_and_qa(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/milo/jobs",
        json={
            "objective": "Calculate 6 * 7. Publish nothing.",
            "title": "Arithmetic",
            "constraints": ["Publish nothing", "Do not invent a price"],
        },
        headers={"Idempotency-Key": "constraints-job"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["constraints"] == ["Publish nothing", "Do not invent a price"]
    assert body["mode"] == "SIMULATED"
    project_id = body["project_id"]
    status = client.get(f"/milo/jobs/{project_id}")
    assert status.status_code == 200
    assert status.json()["mode"] == "SIMULATED"
    assert status.json()["constraints"] == ["Publish nothing", "Do not invent a price"]
    qa = next(task for task in list_tasks(project_id) if task["owner"] == "qa")
    assert "Hard constraint: Publish nothing" in qa["acceptance_criteria"]
    assert "Hard constraint: Do not invent a price" in qa["acceptance_criteria"]
    handover = list_handovers(project_id)[0]
    assert handover["from_role"] == "director"
    assert "Hard constraint: Publish nothing" in handover["acceptance_criteria"]
    programme = Programme(project_id, "Calculate 6 * 7. Publish nothing.")
    child = {"id": "unused-child", "report": "The product is 42."}
    assert "Hard constraint" in _manager_user(programme)
    assert "Publish nothing" in _manager_user(programme)
    assert "Do not invent a price" in _supervisor_user(programme, child)


def test_a_string_constraint_is_kept(monkeypatch):
    client = _client(monkeypatch)
    created = client.post(
        "/milo/jobs",
        json={"objective": "Calculate 6 * 7.", "constraints": "Publish nothing"},
        headers={"Idempotency-Key": "constraint-string"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["constraints"] == ["Publish nothing"]


def test_same_key_different_title_is_a_duplicate_and_a_new_key_reruns(monkeypatch):
    client = _client(monkeypatch)
    headers = {"Idempotency-Key": "same-job"}
    first = client.post("/milo/jobs", json={"objective": "Calculate 6 * 7.", "title": "First title"}, headers=headers)
    second = client.post("/milo/jobs", json={"objective": "Calculate 6 * 7.", "title": "Renamed"}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["project_id"] == first.json()["project_id"]
    assert client.post(
        "/milo/jobs",
        json={"objective": "Calculate 9 * 9.", "title": "Other"},
        headers=headers,
    ).status_code == 409
    rerun = client.post(
        "/milo/jobs",
        json={"objective": "Calculate 6 * 7.", "title": "First title"},
        headers={"Idempotency-Key": "explicit-rerun"},
    )
    assert rerun.status_code == 200
    assert rerun.json()["duplicate"] is False
    assert rerun.json()["project_id"] != first.json()["project_id"]


def test_missing_mode_reads_as_unverified(monkeypatch):
    from app.db import connect

    client = _client(monkeypatch)
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at, run_mode) VALUES(?,?,?,?,?,?)",
        ("legacy-job", "Legacy", "Calculate 1 + 1.", "running", "2026-10-13T00:00:00+00:00", None),
    )
    conn.commit()
    conn.close()
    status = client.get("/milo/jobs/legacy-job")
    assert status.status_code == 200
    assert status.json()["mode"] == "UNVERIFIED"
