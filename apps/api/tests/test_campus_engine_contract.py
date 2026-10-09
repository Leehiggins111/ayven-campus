from fastapi.testclient import TestClient
from app.main import app
from app.db import reset_connection_state


def test_campus_alias_uses_atomic_engine_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("AYVEN_DB", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("AYVEN_ACCESS_KEY", "campus-test-secret")
    monkeypatch.delenv("AYVEN_API_TOKEN", raising=False)
    monkeypatch.setattr("app.orchestrator.run_project", lambda pid: None)
    reset_connection_state()
    client = TestClient(app)
    url = "/milo/jobs?idempotency_key=stable-job"
    body = {"objective": "Draft a kitchen refresh advert"}
    assert client.post(url, json=body).status_code == 401
    headers = {"Authorization": "Bearer campus-test-secret"}
    first = client.post(url, json=body, headers=headers).json()
    second = client.post(url, json=body, headers=headers).json()
    assert first["project_id"] == second["project_id"]
    assert second["duplicate"] is True
    assert client.post(url, json={"objective": "Different job"}, headers=headers).status_code == 409
    assert client.post(url, json=body, headers={**headers,"Idempotency-Key":"other-job"}).status_code == 400
    assert client.get("/milo/jobs/" + first["project_id"], headers=headers).status_code == 200
    for route in ["/state", "/campus/view", "/approvals", "/admin/backup"]:
        assert client.get(route).status_code == 401
    monkeypatch.setenv("AYVEN_READ_TOKEN", "read-only")
    assert client.get("/admin/backup", headers={"Authorization": "Bearer read-only"}).status_code == 401
    reset_connection_state()


def test_vague_job_module_imports_and_asks_subject():
    from app.intelligence.clarification import blocking_question
    assert "subject" in blocking_question("research it")
    assert blocking_question("NEED: Which device?") == "Which device?"
