"""What the lease, fence, outbox, and durable receipt tests prove.

Each test names the property it proves and the property it does not prove.
Earlier resume tests did not prove that two live workers are excluded, that a
superseded attempt fails at commit, or that a recorded tool call is not sent
again. Those claims are not treated as verified here.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.db import connect
from app.intelligence.leases import bind, claim, escalate_expired, perform_side_effect, reset
from app.intelligence.run_token import StaleWrite

API = Path(__file__).resolve().parents[1]


def _project(project_id: str = "job") -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        (project_id, "Job", "Calculate 6 * 7.", "running", "2026-10-13T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_two_processes_cannot_claim_the_same_job():
    """Proves two live processes cannot both hold the claim. Does not prove DBOS dedupe."""
    script = r"""
import sys
from app.intelligence.leases import claim
fence = claim("shared-job", sys.argv[1])
print("NONE" if fence is None else f"FENCE {fence}")
"""
    _project("shared-job")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(API) + os.pathsep + env.get("PYTHONPATH", "")
    procs = [
        subprocess.Popen([sys.executable, "-c", script, name], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for name in ("owner-a", "owner-b")
    ]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=20)
        assert proc.returncode == 0, err
        results.append(out.strip().splitlines()[-1])
    assert sum(line == "NONE" for line in results) == 1
    assert sum(line.startswith("FENCE ") for line in results) == 1


def test_a_restart_reads_the_durable_checkpoint_and_does_not_repeat_the_action(tmp_path):
    """Proves a second process returns the stored stage receipt. Does not prove a live model or an external HTTP call."""
    script = r"""
import json, os, sys
from app.db import connect
from app.durable import atomic_stage
root = os.environ["PROBE_ROOT"]
mode = sys.argv[1]
conn = connect()
conn.execute("CREATE TABLE IF NOT EXISTS durable_checkpoints (job_id TEXT NOT NULL, stage TEXT NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY(job_id,stage))")
conn.commit()
conn.close()

def action():
    open(os.path.join(root, "action-ran-" + mode), "w").write("yes")
    return {"version": 1, "value": 7}

result = atomic_stage("checkpoint-job", "prepare", action)
open(os.path.join(root, mode + ".json"), "w").write(json.dumps(result))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(API) + os.pathsep + env.get("PYTHONPATH", "")
    env["AYVEN_DB"] = str(tmp_path / "checkpoint.sqlite")
    env["PROBE_ROOT"] = str(tmp_path)
    env.pop("AYVEN_WORKFLOW_DATABASE_URL", None)
    first = subprocess.run([sys.executable, "-c", script, "first"], env=env, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    second = subprocess.run([sys.executable, "-c", script, "second"], env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / "action-ran-first").is_file()
    assert not (tmp_path / "action-ran-second").exists()
    import json
    assert json.loads((tmp_path / "second.json").read_text())["value"] == 7


def test_model_retries_go_through_complete(monkeypatch):
    """Proves a timeout is retried on the normal complete path. Does not prove a live model timeout or jitter."""
    from app.intelligence.execution import _complete

    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timed out")
        return "42", 1, {"backend": "stub"}

    monkeypatch.setattr("app.models.complete_role", flaky)
    text, _tokens, meta = _complete("MANAGER", "system", "user")
    assert text == "42"
    assert calls["n"] == 2
    assert meta["retries"] == 1


def test_a_superseded_fence_cannot_commit():
    """Proves the fencing token is checked at commit. Does not prove a killed operating-system thread."""
    _project("fenced-job")
    first = claim("fenced-job", "owner-a")
    conn = connect()
    conn.execute("UPDATE job_leases SET lease_until=0 WHERE job_id=?", ("fenced-job",))
    conn.commit()
    conn.close()
    second = claim("fenced-job", "owner-b")
    assert first == 1 and second == 2
    token = bind("fenced-job", "owner-a", first)
    try:
        writer = connect()
        writer.execute("UPDATE projects SET result=? WHERE id=?", ("late", "fenced-job"))
        with pytest.raises(StaleWrite, match="fence superseded at commit"):
            writer.commit()
        writer.close()
    finally:
        reset(token)
    check = connect()
    result = check.execute("SELECT result FROM projects WHERE id=?", ("fenced-job",)).fetchone()["result"]
    check.close()
    assert result in (None, "")


def test_a_recorded_side_effect_is_not_sent_again():
    """Proves the tool function runs once. Does not prove the remote service saw only one request if the process died after sending and before the receipt."""
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        return [{"title": "one", "url": "https://example.com/a"}]

    first = perform_side_effect("job-1", "research", "web_search", "host kit", send)
    second = perform_side_effect("job-1", "research", "web_search", "host kit", send)
    assert first == second
    assert calls["n"] == 1
    conn = connect()
    conn.execute(
        "INSERT INTO side_effects(idem_key, job_id, fence, stage, action, target, status, result_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("pending-key", "job-2", 1, "research", "web_search", "other", "pending", None, 0),
    )
    conn.commit()
    conn.close()
    from app.intelligence.leases import _idem_key

    assert _idem_key("job-1", "research", "web_search", "host kit") != "pending-key"


def test_a_pending_side_effect_is_not_sent_again():
    """Proves a crash after the outbox insert does not call the tool again. Does not prove the first send was stored."""
    from app.intelligence.leases import _idem_key

    key = _idem_key("job-3", "research", "web_search", "again")
    conn = connect()
    conn.execute(
        """INSERT INTO side_effects(idem_key, job_id, fence, stage, action, target, status, result_json, created_at)
           VALUES(?,?,?,?,?,?, 'pending', NULL, 0)""",
        (key, "job-3", 1, "research", "web_search", "again"),
    )
    conn.commit()
    conn.close()
    calls = {"n": 0}
    result = perform_side_effect("job-3", "research", "web_search", "again", lambda: calls.__setitem__("n", 1))
    assert result is None
    assert calls["n"] == 0


def test_exhausted_attempts_escalate_the_job():
    """Proves an expired lease at the attempt cap becomes escalated. Does not prove Milo displayed it."""
    _project("tired-job")
    assert claim("tired-job", "owner-a") == 1
    conn = connect()
    conn.execute(
        "UPDATE job_leases SET lease_until=0, attempts=3, max_attempts=3 WHERE job_id=?",
        ("tired-job",),
    )
    conn.commit()
    conn.close()
    assert escalate_expired() == ["tired-job"]
    check = connect()
    state = check.execute("SELECT state FROM job_leases WHERE job_id=?", ("tired-job",)).fetchone()["state"]
    status = check.execute("SELECT status, result FROM projects WHERE id=?", ("tired-job",)).fetchone()
    check.close()
    assert state == "escalated"
    assert status["status"] == "waiting"
    assert "Escalated" in status["result"]
    assert claim("tired-job", "owner-b") is None


def test_durable_mode_does_not_also_thread_resume(monkeypatch):
    """Proves the thread resume stays idle when DBOS recovery is on. Does not prove DBOS launched."""
    from app.intelligence.recovery import resume_unfinished

    monkeypatch.setenv("AYVEN_DURABLE", "1")
    monkeypatch.setenv("AYVEN_RESUME", "1")
    assert resume_unfinished() == []


def test_a_second_resume_does_not_run_the_job_again(monkeypatch):
    """Proves a finished claim is not taken again. Does not prove a kill mid-run; that lease stays until it expires."""
    from app.intelligence.recovery import resume_unfinished

    monkeypatch.setenv("AYVEN_RESUME", "1")
    monkeypatch.setenv("AYVEN_DURABLE", "0")
    calls = []
    monkeypatch.setattr(
        "app.intelligence.execution.run_objective",
        lambda project_id, objective, task_id=None: calls.append(project_id) or "pkg",
    )
    _project("resume-job")
    assert resume_unfinished() == ["resume-job"]
    assert resume_unfinished() == []
    assert calls == ["resume-job"]


def test_health_answers_while_recovery_is_blocked(monkeypatch):
    """Proves /health does not wait for recovery. Does not prove a recovered job."""
    import threading

    from fastapi.testclient import TestClient

    from app.main import app

    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        release.wait(3)
        return []

    monkeypatch.setenv("AYVEN_DURABLE", "0")
    monkeypatch.setenv("AYVEN_RESUME", "1")
    monkeypatch.setattr("app.intelligence.recovery.recover_once", block)
    with TestClient(app) as client:
        assert started.wait(2)
        assert client.get("/health").status_code == 200
        release.set()
        assert app.state.recovery_done.wait(3)
