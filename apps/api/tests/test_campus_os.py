"""Campus operating picture: real state, approval resume, clarification, repairs."""

import json
import uuid

from fastapi.testclient import TestClient

from app.db import connect
from app.intelligence.claims import add_claim
from app.intelligence.execution import run_objective
from app.intelligence.repair import apply_repairs
from app.main import app
from app.orchestrator import resolve_approval
from app.version import GPU_VALIDATED, __version__


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-09-28T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def _parent(package_id: str) -> dict:
    conn = connect()
    row = dict(conn.execute("SELECT * FROM work_packages WHERE id=?", (package_id,)).fetchone())
    conn.close()
    return row


def test_version_is_pre_gpu_campus():
    assert __version__ == "2.0.2"
    assert GPU_VALIDATED is False


def test_agent_visual_states_are_emitted_on_the_production_path():
    parent = run_objective(_project("What is the sum of 12 and 30?"), "What is the sum of 12 and 30?")
    conn = connect()
    visuals = [row["status"] for row in conn.execute("SELECT status FROM events WHERE type='agent.visual'").fetchall()]
    stage = conn.execute("SELECT campus_stage, workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone()
    conn.close()
    for name in ("PLANNING", "WRITING", "REVIEWING"):
        assert name in visuals
    assert stage["workflow_state"] in ("COMPLETED", "AWAITING_APPROVAL")
    assert stage["campus_stage"] in ("COMPLETE", "APPROVAL")
    conn = connect()
    findings = conn.execute("SELECT findings FROM work_packages WHERE id=?", (parent,)).fetchone()["findings"]
    conn.close()
    assert "42.00" in (findings or "")
    client = TestClient(app)
    view = client.get("/campus/view", params={"package_id": parent}).json()
    assert view["package_id"] == parent
    assert view["cost"] == "unknown"
    assert any(step["state"] == "current" for step in view["workflow"])


def test_http_approve_resumes_the_same_package_and_direct_resolve_stays_approved():
    objective = "Research the public market for office supplies and name the gaps."
    project = _project(objective)
    parent = run_objective(project, objective, "task-campus-approve")
    conn = connect()
    approval = conn.execute("SELECT id FROM approvals WHERE task_id=?", ("task-campus-approve",)).fetchone()
    conn.close()
    assert approval
    assert _parent(parent)["workflow_state"] == "AWAITING_APPROVAL"
    resolve_approval(approval["id"], "approved")
    assert _parent(parent)["workflow_state"] == "APPROVED"
    client = TestClient(app)
    project2 = _project(objective)
    parent2 = run_objective(project2, objective, "task-campus-http")
    conn = connect()
    approval2 = conn.execute("SELECT id FROM approvals WHERE task_id=?", ("task-campus-http",)).fetchone()
    conn.close()
    resolved = client.post(f"/approvals/{approval2['id']}/resolve", json={"decision": "approved"})
    assert resolved.status_code == 200
    assert resolved.json()["resumed"]
    after = _parent(parent2)
    assert after["id"] == parent2
    assert after["workflow_state"] == "COMPLETED"
    assert after["campus_stage"] == "COMPLETE"
    view = client.get("/campus/view", params={"package_id": parent2}).json()
    assert view["finished"] is True
    assert view["needs_you"] is False
    assert view["result"]["manager"]


def test_http_reject_lands_in_the_rejected_state():
    objective = "Research the public market for office supplies and name the gaps."
    project = _project(objective)
    parent = run_objective(project, objective, "task-campus-reject")
    conn = connect()
    approval = conn.execute("SELECT id FROM approvals WHERE task_id=?", ("task-campus-reject",)).fetchone()
    conn.close()
    client = TestClient(app)
    resolved = client.post(f"/approvals/{approval['id']}/resolve", json={"decision": "rejected"})
    assert resolved.status_code == 200
    after = _parent(parent)
    assert after["workflow_state"] == "FAILED"
    assert after["campus_stage"] == "REJECTED"
    view = client.get("/campus/view", params={"package_id": parent}).json()
    assert view["rejected"] is True
    assert view["needs_you"] is False
    assert view["result"]["cost"] == "unknown"
    approval_row = connect()
    status = approval_row.execute("SELECT status FROM approvals WHERE id=?", (approval["id"],)).fetchone()["status"]
    approval_row.close()
    assert status == "rejected"


def test_clarification_answer_continues_the_same_package():
    objective = "Compare public suppliers of office stationery.\nNEED: which city should the search cover?"
    project = _project(objective)
    parent = run_objective(project, objective, "task-clarify")
    row = _parent(parent)
    assert row["workflow_state"] == "AWAITING_CLARIFICATION"
    assert "city" in (row["clarification_question"] or "")
    conn = connect()
    parents = conn.execute("SELECT id FROM work_packages WHERE project_id=? AND parent_id IS NULL", (project,)).fetchall()
    conn.close()
    assert len(parents) == 1
    client = TestClient(app)
    view = client.get("/campus/view", params={"package_id": parent}).json()
    assert view["needs_clarification"] is True
    assert "city" in view["question"]
    assert any(step["id"] == "CLARIFICATION" and step["state"] == "current" for step in view["workflow"])
    resumed = client.post(f"/work-packages/{parent}/clarification", json={"answer": "the north office"})
    assert resumed.status_code == 200
    assert resumed.json()["package_id"] == parent
    after = _parent(parent)
    assert after["clarification_answer"] == "the north office"
    assert after["workflow_state"] != "AWAITING_CLARIFICATION"
    assert after["workflow_state"] in ("AWAITING_APPROVAL", "COMPLETED", "REPAIRING", "READY")
    conn = connect()
    parents = conn.execute("SELECT id FROM work_packages WHERE project_id=? AND parent_id IS NULL", (project,)).fetchall()
    conn.close()
    assert [item["id"] for item in parents] == [parent]
    refused = client.post(f"/work-packages/{parent}/clarification", json={"answer": "too late"})
    assert refused.status_code == 400


def test_repair_and_evidence_views_use_the_ledger():
    objective = "Research the public market for office supplies and name the gaps."
    parent = run_objective(_project(objective), objective, "task-repair-view")
    claim = add_claim(parent, "research-e1", "Footfall is 90000.", "FACT", evidence_text="A centre exists.", source_type="UNKNOWN")
    second = add_claim(parent, "research-e1", "A second unverified total is 90000.", "FACT", evidence_text="A centre exists.", source_type="UNKNOWN")
    report = apply_repairs(parent, [
        {"claim_id": claim["id"], "result": "DISPROVED", "challenge": "was footfall stated", "resolution": "Footfall was not in the opened page.", "evidence": "A centre exists."},
        {"claim_id": second["id"], "result": "DISPROVED", "challenge": "was footfall stated", "resolution": "Footfall was not in the opened page.", "evidence": "A centre exists."},
    ], cap=1)
    assert len(report["actions"]) == 2
    conn = connect()
    raw = conn.execute("SELECT observability_json FROM work_packages WHERE id=?", (parent,)).fetchone()["observability_json"]
    obs = json.loads(raw or "{}")
    obs["repairs"] = report["actions"]
    conn.execute("UPDATE work_packages SET observability_json=? WHERE id=?", (json.dumps(obs), parent))
    conn.commit()
    conn.close()
    conn = connect()
    visuals = [row["status"] for row in conn.execute("SELECT status FROM events WHERE type='agent.visual'").fetchall()]
    conn.close()
    assert "RESEARCHING" in visuals
    assert "USING_TOOL" in visuals
    view = TestClient(app).get("/campus/view", params={"package_id": parent}).json()
    assert view["repairing"] is True
    assert len(view["repairs"]) == 2
    assert view["repairs"][0]["repair_type"]
    assert view["repairs"][0]["resolution"]
    assert "<think" not in json.dumps(view["repairs"]).lower()
    assert view["evidence"]
    assert any(item["support"] for item in view["evidence"])
