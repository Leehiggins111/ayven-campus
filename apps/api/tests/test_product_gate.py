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
    from app.intelligence.execution import wait_for_clarification

    assert wait_for_clarification(parent, timeout=90)
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


def test_ollama_requests_are_plain_chat():
    from app.models import local_request_body

    guided = {"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16, "temperature": 0.2, "guided_json": {"type": "object"}, "grammar": "root ::= "}
    plain = local_request_body("http://127.0.0.1:11434/v1", guided)
    assert "guided_json" not in plain
    assert "grammar" not in plain
    assert plain["think"] is False
    assert plain["reasoning_effort"] == "none"
    assert local_request_body("http://127.0.0.1:9/v1", guided)["guided_json"]["type"] == "object"


def test_local_model_is_used_when_the_stub_is_off(monkeypatch):
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AYVEN_ALLOW_ESCALATION", "0")
    monkeypatch.setenv("AYVEN_EMPLOYEE_MODEL", "qwen3:4b")

    def fake(base, key, model, system, user, max_tokens, schema=None):
        assert base.startswith("http://127.0.0.1:11434")
        assert model == "qwen3:4b"
        return "The local model answered.", 4, {}

    monkeypatch.setattr("app.models._openai_compat", fake)
    from app.llm import complete

    text, tokens = complete("sys", "Calculate 6 * 7")
    assert text == "The local model answered."
    assert tokens == 4
    assert "stub" not in text.lower()


def test_business_plan_lists_the_owner_sections_without_inventing_a_price():
    from app.intelligence.render import render_focus

    text = render_focus("draft", {
        "task_class": "business_research",
        "objective": "Create a business launch plan for Alba Kitchen Refresh.",
        "research": {"evidence": [], "gaps": []},
    })
    for section in (
        "Service",
        "Target customer",
        "Problem",
        "Offer and positioning",
        "Competitor and market research",
        "Pricing",
        "Channels",
        "Advert",
        "Call to action",
        "Next steps",
        "Assumptions",
        "Unresolved",
    ):
        assert section in text
    assert "Nothing was sent" in text
    assert "£" not in text
    assert "UNKNOWN · UNKNOWN" not in text


def test_research_synthesis_still_waits_for_approval():
    from app.models import set_role_generator

    def gen(role, system, user, max_tokens):
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: publish the briefing", 3, {"backend": "generator"}
        if role == "SUPERVISOR":
            return "ACCEPT", 2, {"backend": "generator"}
        return "notes", 2, {"backend": "generator"}

    set_role_generator(gen)
    try:
        parent = run_objective(
            _project("Research the public market for office supplies and name the gaps."),
            "Research the public market for office supplies and name the gaps.",
            "task-synth-gate",
        )
    finally:
        set_role_generator(None)
    conn = connect()
    state = conn.execute("SELECT workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone()["workflow_state"]
    conn.close()
    assert state == "AWAITING_APPROVAL"


def test_access_stays_off_when_the_key_is_unset(monkeypatch):
    monkeypatch.delenv("AYVEN_ACCESS_KEY", raising=False)
    os.environ.pop("AYVEN_ACCESS_KEY", None)
    health = TestClient(app).get("/health").json()
    assert health["access_protected"] is False


def test_stored_offer_line_is_not_cut_mid_word():
    from app.intelligence.campus_view import owner_findings
    from app.intelligence.deliverable import clip_at_boundary
    from app.intelligence.render import render_focus

    offer = "Unresolved. No opened page stated an offer or how it differs from other providers."
    clipped = clip_at_boundary(offer, 74)
    assert clipped.endswith("other")
    assert not clipped.endswith("pr")
    assert "providers." in offer
    plan = render_focus("draft", {
        "task_class": "business_research",
        "objective": "Create a business launch plan for Alba Kitchen Refresh.",
        "research": {"evidence": [], "gaps": []},
    })
    stored = owner_findings(plan)
    assert "how it differs from other providers." in stored


def test_live_employee_asks_through_the_closed_think_client(monkeypatch):
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    seen = {}

    def fake_complete(role, system, user, max_tokens=500, schema=None):
        seen["role"] = role
        seen["max_tokens"] = max_tokens
        return (
            "Offer and positioning\nThe offer is an on-site cabinet respray.",
            40,
            {"backend": "local_openai_compat", "elapsed_s": 1.2},
        )

    monkeypatch.setattr("app.models.complete_role", fake_complete)
    from app.intelligence.qwen_adapter import employee_turn

    turned = employee_turn(
        system="Write the plan.",
        user="Alba Kitchen Refresh",
        agent_id="research-e1",
        package_id="pkg-live-native",
        preset_text=None,
        max_tokens=2048,
        handler=lambda tool, payload: "noted",
    )
    assert seen["role"] == "EMPLOYEE"
    assert seen["max_tokens"] == 2048
    assert "on-site cabinet respray" in turned["text"]
    assert "SECRET" not in turned["text"]
    assert turned["qwen_mode"] == "live"
    assert turned["meta"]["backend"] == "local_openai_compat"
    assert turned["meta"]["completion_tokens"] == 40


def test_ollama_answer_keeps_only_the_text_after_think():
    from app.models import ollama_answer

    text = ollama_answer({
        "response": "I should think about the offer\n</think>\nOffer and positioning\nThe offer is an on-site cabinet respray.",
        "thinking": "SECRET_REASONING",
    })
    assert text == "Offer and positioning\nThe offer is an on-site cabinet respray."
    assert "I should think" not in text
    assert "SECRET_REASONING" not in text
    assert ollama_answer({"response": "", "thinking": "SECRET_REASONING the offer is half"}) == ""


def test_ollama_native_generate_does_not_publish_reasoning(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "body": json})
        if len(calls) == 1:
            return Response({"response": "", "thinking": "SECRET_REASONING the offer is half", "eval_count": 40})
        return Response({
            "response": "I should think about the offer\n</think>\nOffer and positioning\nThe offer is an on-site cabinet respray.",
            "thinking": "SECRET_REASONING",
            "eval_count": 80,
        })

    monkeypatch.setattr("httpx.post", fake_post)
    from app.models import _openai_compat

    text, _tokens, info = _openai_compat(
        "http://127.0.0.1:11434/v1", "local", "qwen3:4b", "Write the plan.", "Alba Kitchen Refresh", 2048,
    )
    assert "SECRET_REASONING" not in text
    assert "I should think" not in text
    assert "on-site cabinet respray" in text
    assert info["postcheck"] == "skipped"
    assert calls[0]["url"].endswith("/api/generate")
    first = calls[0]["body"]
    assert first["raw"] is True
    assert first["think"] is False
    assert first["stream"] is False
    assert "grammar" not in first
    assert "guided_json" not in first
    assert "response_format" not in first
    assert "messages" not in first
    assert first["options"]["num_predict"] == 2048
    assert first["options"]["num_ctx"] >= 8192
    assert first["prompt"].rstrip().endswith("</think>")
    assert "/no_think" in first["prompt"]
    assert "<|im_start|>assistant\n<think>\n\n</think>" in first["prompt"]
    assert calls[1]["body"]["think"] is False
    assert calls[1]["body"]["options"]["num_predict"] == 4096
    assert calls[1]["body"]["options"]["num_predict"] > first["options"]["num_predict"]
