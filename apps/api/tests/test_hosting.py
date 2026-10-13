"""Hosting rules: Turso is optional, files live in the DB, sessions expire, jobs resume."""

import time
import uuid

from fastapi.testclient import TestClient

from app.access import TTL, session_value, validate_public_configuration
from app.blobs import load_bytes, save_bytes
from app.db import connect, reset_connection_state
from app.distribution import update_package
from app.intelligence.observability import record_trace, traces_for
from app.intelligence.recovery import resume_unfinished
from app.intelligence.store import insert_package, save_model_call
from app.main import app


def test_a_database_url_without_a_token_or_driver_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("AYVEN_DB_URL", "libsql://ayven-campus.example.turso.io")
    monkeypatch.delenv("AYVEN_DB_AUTH_TOKEN", raising=False)
    reset_connection_state()
    try:
        connect()
        raise AssertionError("URL without a token must not open SQLite")
    except RuntimeError as exc:
        assert "AYVEN_DB_AUTH_TOKEN" in str(exc)
    monkeypatch.setenv("AYVEN_DB_AUTH_TOKEN", "database-scoped-token")
    reset_connection_state()
    try:
        connect()
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "libsql" in str(exc).lower() or "AYVEN_DB_URL" in str(exc)
    assert raised


def test_files_and_traces_survive_a_process_restart():
    package_id = "restart-" + uuid.uuid4().hex
    save_bytes(package_id, "listing.md", b"draft listing", "deliverable")
    record_trace(package_id, "note", {"detail": "kept-across-restart"})
    reset_connection_state()
    assert load_bytes(package_id, "listing.md") == b"draft listing"
    events = traces_for(package_id)
    assert any(row["event"] == "note" for row in events)


def test_cors_wildcard_is_rejected_and_a_named_origin_is_allowed(monkeypatch):
    monkeypatch.setenv("AYVEN_CORS_ORIGINS", "*")
    try:
        validate_public_configuration()
        raise AssertionError("wildcard CORS must be rejected")
    except RuntimeError as exc:
        assert "wildcard" in str(exc).lower() or "AYVEN_CORS_ORIGINS" in str(exc)
    monkeypatch.setenv("AYVEN_CORS_ORIGINS", "https://ayven.example")
    validate_public_configuration()
    monkeypatch.setenv("AYVEN_CORS_ORIGINS", "")
    validate_public_configuration()


def test_a_session_older_than_twelve_hours_is_refused(monkeypatch):
    monkeypatch.setenv("AYVEN_ACCESS_KEY", "k" * 40)
    stale = session_value(int(time.time()) - TTL - 60)
    fresh = session_value(int(time.time()))
    client = TestClient(app)
    denied = client.get("/state", cookies={"ayven_session": stale})
    allowed = client.get("/state", cookies={"ayven_session": fresh})
    assert denied.status_code == 401
    assert allowed.status_code == 200


def test_a_running_job_resumes_after_the_worker_sleeps(monkeypatch):
    monkeypatch.setenv("AYVEN_RESUME", "1")
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        (project_id, "sleep", "What is the sum of 12 and 30?", "running", "2026-10-13T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    parent = insert_package(
        project_id=project_id,
        title="Research programme",
        objective="What is the sum of 12 and 30?",
        origin="milo",
        agent_id="research-mgr",
        tier="MANAGER",
        model_role="MANAGER",
    )
    update_package(parent, workflow_state="IN_PROGRESS", findings="42.00")
    save_model_call(parent, "MANAGER", "calculation", {"backend": "stub"}, "SYNTHESISE")
    before = 1
    resumed = resume_unfinished()
    assert project_id in resumed
    conn = connect()
    status = conn.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()["status"]
    calls = conn.execute(
        "SELECT COUNT(*) AS n FROM model_calls WHERE package_id=? AND role='MANAGER'",
        (parent,),
    ).fetchone()["n"]
    conn.close()
    assert status != "running"
    assert calls == before
