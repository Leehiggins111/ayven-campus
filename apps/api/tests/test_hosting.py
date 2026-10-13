"""Hosting rules: Turso is optional, files live in the DB, sessions expire, jobs resume."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.access import TTL, session_value, validate_public_configuration
from app.blobs import load_bytes, save_bytes
from app.db import connect, reset_connection_state
from app.distribution import update_package
from app.intelligence.observability import record_trace, traces_for
from app.intelligence.recovery import resume_unfinished
from app.intelligence.store import insert_package, save_model_call
from app.main import app


def test_a_database_url_without_a_token_does_not_fall_back(monkeypatch, tmp_path):
    """A remote URL never opens the local file, and a dead port is not a silent SQLite success."""
    local = tmp_path / "fallback.sqlite"
    monkeypatch.setenv("AYVEN_DB", str(local))
    monkeypatch.setenv("AYVEN_DB_URL", "http://127.0.0.1:9")
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
        raise AssertionError("an unreachable remote database must not open SQLite")
    except Exception as exc:
        assert "sqlite" not in str(exc).lower() or "libsql" in str(exc).lower()
    assert not local.exists()


def test_hosted_mode_refuses_to_start_without_a_remote_database(monkeypatch, tmp_path):
    from app.access import validate_public_configuration
    from app.db import persistence_report

    local = tmp_path / "hosted.sqlite"
    monkeypatch.setenv("AYVEN_DB", str(local))
    monkeypatch.setenv("AYVEN_REQUIRE_DURABLE", "1")
    monkeypatch.delenv("AYVEN_DB_URL", raising=False)
    monkeypatch.delenv("AYVEN_DB_AUTH_TOKEN", raising=False)
    reset_connection_state()
    try:
        validate_public_configuration()
        raise AssertionError("hosted mode must refuse startup")
    except RuntimeError as exc:
        assert "AYVEN_DB_URL" in str(exc)
        assert "Temporary SQLite" in str(exc)
    try:
        connect()
        raise AssertionError("hosted mode must not open SQLite")
    except RuntimeError as exc:
        assert "Temporary SQLite" in str(exc)
    assert not local.exists()
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("AYVEN_REQUIRE_DURABLE", raising=False)
    monkeypatch.setenv("AYVEN_DB_URL", "file:" + str(tmp_path / "local.db"))
    monkeypatch.setenv("AYVEN_DB_AUTH_TOKEN", "token")
    try:
        validate_public_configuration()
        raise AssertionError("a file URL is not a hosted database")
    except RuntimeError as exc:
        assert "file:" in str(exc)
    report = persistence_report()
    monkeypatch.delenv("AYVEN_DB_URL", raising=False)
    monkeypatch.delenv("AYVEN_DB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert persistence_report()["durable"] is False
    assert persistence_report()["persistence_state"] == "not_durable"
    assert report["persistence"] == "libsql_file"


def test_health_reports_local_sqlite_as_not_durable():
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["durable"] is False
    assert body["persistence_state"] == "not_durable"
    assert body["persistence"] == "local_sqlite"


def _sqld_binary() -> str:
    candidates = [
        os.environ.get("AYVEN_SQLD", ""),
        "/tmp/sqld/libsql-server-x86_64-unknown-linux-gnu/sqld",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return ""


def test_remote_hrana_rows_survive_a_new_process_and_health_is_durable(tmp_path, monkeypatch):
    """A local sqld stands in for Turso. The client uses HTTP, not embedded sync."""
    binary = _sqld_binary()
    if not binary:
        pytest.skip("sqld is not installed; remote libsql is implemented and awaiting a live server")
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    db_dir = tmp_path / "sqld-data"
    db_dir.mkdir()
    proc = subprocess.Popen(
        [binary, "--db-path", str(db_dir / "db"), "--http-listen-addr", f"127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(proc.stdout.read() if proc.stdout else "sqld exited")
            try:
                import urllib.request

                with urllib.request.urlopen(url + "/v2", timeout=0.5) as response:
                    if 200 <= response.status < 300:
                        ready = True
                        break
            except Exception:
                time.sleep(0.1)
        assert ready, "sqld did not open /v2"
        monkeypatch.setenv("AYVEN_DB_URL", url)
        monkeypatch.setenv("AYVEN_DB_AUTH_TOKEN", "local-sqld-token")
        monkeypatch.setenv("AYVEN_REQUIRE_DURABLE", "1")
        reset_connection_state()
        from app.db import persistence_report

        report = persistence_report()
        assert report["durable"] is True
        assert report["persistence"] == "libsql_http"
        assert report["persistence_state"] == "durable"
        conn = connect()
        conn.execute(
            "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
            ("remote-job", "Remote", "Calculate 6 * 7.", "running", "2026-10-13T00:00:00+00:00"),
        )
        conn.commit()
        row = conn.execute("SELECT id, title FROM projects WHERE id=?", ("remote-job",)).fetchone()
        assert row["id"] == "remote-job"
        assert row[0] == "remote-job"
        assert dict(row)["title"] == "Remote"
        conn.close()
        script = r"""
from app.db import connect, reset_connection_state
reset_connection_state()
conn = connect()
row = conn.execute("SELECT id, title FROM projects WHERE id=?", ("remote-job",)).fetchone()
assert row["id"] == "remote-job", row
assert dict(row)["title"] == "Remote"
print("survived")
conn.close()
"""
        env = os.environ.copy()
        env["AYVEN_DB_URL"] = url
        env["AYVEN_DB_AUTH_TOKEN"] = "local-sqld-token"
        env["AYVEN_REQUIRE_DURABLE"] = "1"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "survived" in result.stdout
        client = TestClient(app)
        body = client.get("/health").json()
        assert body["durable"] is True
        assert body["persistence_state"] == "durable"
        assert body["persistence"] == "libsql_http"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        reset_connection_state()


def test_libsql_file_rows_survive_a_new_process(tmp_path, monkeypatch):
    """A real libsql file accepts row['id'] and dict(row), and the row is still there in a new process."""
    url = "file:" + str(tmp_path / "campus.db")
    monkeypatch.setenv("AYVEN_DB_URL", url)
    monkeypatch.delenv("AYVEN_DB_AUTH_TOKEN", raising=False)
    reset_connection_state()
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        ("libsql-job", "Libsql", "Calculate 6 * 7.", "running", "2026-10-13T00:00:00+00:00"),
    )
    conn.commit()
    row = conn.execute("SELECT id, title FROM projects WHERE id=?", ("libsql-job",)).fetchone()
    assert row["id"] == "libsql-job"
    assert row[0] == "libsql-job"
    assert dict(row)["title"] == "Libsql"
    conn.close()
    script = r"""
from app.db import connect, reset_connection_state
reset_connection_state()
conn = connect()
row = conn.execute("SELECT id, title FROM projects WHERE id=?", ("libsql-job",)).fetchone()
assert row["id"] == "libsql-job", row
assert dict(row)["title"] == "Libsql"
print("survived")
conn.close()
"""
    env = os.environ.copy()
    env["AYVEN_DB_URL"] = url
    env.pop("AYVEN_DB_AUTH_TOKEN", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "survived" in result.stdout
    reset_connection_state()


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
    monkeypatch.setenv("AYVEN_THREAD_STUB", "1")
    monkeypatch.setenv("AYVEN_ENV", "local")
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
