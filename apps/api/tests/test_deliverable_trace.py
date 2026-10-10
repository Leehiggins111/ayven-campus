"""Each hop from the model reply to the screen keeps the plan and the program."""

import uuid

from fastapi.testclient import TestClient

from app.db import connect
from app.intelligence.execution import _section_drop_reason, run_objective
from app.intelligence.workflow import log_transition
from app.main import app
from app.models import set_role_generator

DOOR = (
    "Prepare a UK business launch plan for a service that replaces kitchen doors, worktops and handles. "
    "A full kitchen replacement is the expensive alternative. "
    "Painting and wrapping are other services, not this service."
)

_ESSAY = """FILE: main.py
print(2+3)

   print(2+3)

 File:

However, the problem says we should print the sum.

FILE: test_main.py
import subprocess
   result = subprocess.run(['python', 'main.py'], capture_output=True, text=True)
   assert result.stdout.strip() == '5'

However, the problem says not to write assert True.
"""


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-10-10T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def test_generation_record_keeps_finish_reason_tokens_timing_and_verbatim_text(monkeypatch, caplog):
    import logging

    from app.intelligence.store import save_model_call
    from app.models import ollama_generation

    payload = {
        "response": "The service is replacing kitchen doors, worktops and handles.",
        "thinking": "SECRET_REASONING",
        "done_reason": "stop",
        "prompt_eval_count": 18,
        "eval_count": 22,
        "total_duration": 1_500_000_000,
    }
    record = ollama_generation(payload, 9)
    assert record["raw_response"] == payload["response"]
    assert "SECRET_REASONING" not in record["raw_response"]
    assert record["finish_reason"] == "stop"
    assert record["prompt_tokens"] == 18
    assert record["completion_tokens"] == 22
    assert record["elapsed_s"] == 1.5

    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    caplog.set_level(logging.INFO, logger="ayven.generation")
    from app.models import complete_role

    text, tokens, meta = complete_role("EMPLOYEE", "Write the finished lines only.", "State the service.", 80)
    assert "replacing kitchen doors" in text
    assert meta["finish_reason"] == "stop"
    assert meta["prompt_tokens"] == 18
    assert tokens == 22
    assert meta["raw_response"] == payload["response"]
    assert calls and calls[0].endswith("/api/generate")
    assert payload["response"] in caplog.text
    assert "finish_reason=stop" in caplog.text
    package_id = "trace-" + uuid.uuid4().hex[:8]
    save_model_call(package_id, "EMPLOYEE", "business_research", meta, text)
    conn = connect()
    row = dict(conn.execute(
        "SELECT response_text, finish_reason, prompt_tokens, completion_tokens, latency_s FROM model_calls WHERE package_id=?",
        (package_id,),
    ).fetchone())
    conn.close()
    assert row["response_text"] == payload["response"]
    assert row["finish_reason"] == "stop"
    assert row["prompt_tokens"] == 18
    assert row["completion_tokens"] == 22
    assert row["latency_s"] == 1.5


def test_schema_rejection_keeps_the_reply():
    from pydantic import BaseModel

    from app.intelligence.constrained import enforce_output

    class Decision(BaseModel):
        decision: str

    text, info = enforce_output(Decision, "The service is replacing kitchen doors, worktops and handles.")
    assert info["postcheck"] == "rejected"
    assert text == "The service is replacing kitchen doors, worktops and handles."


def test_narration_does_not_drop_the_owner_sentence_and_a_monologue_is_marked_dropped():
    from app.intelligence.deliverable import owner_section

    mixed = (
        "Okay, the user wants a sentence. "
        "The service is replacing kitchen doors, worktops and handles rather than the whole kitchen."
    )
    kept = owner_section("service", mixed)
    assert "replacing kitchen doors, worktops and handles" in kept
    assert "Okay" not in kept
    assert _section_drop_reason("service", mixed, "", DOOR) == ""
    monologue = "Okay, the user wants me to write a section of a launch plan."
    assert owner_section("service", monologue) == ""
    assert _section_drop_reason("service", monologue, "", DOOR) == "sentence_filter"
    assert _section_drop_reason("service", "", "", DOOR) == "empty_generation"


def test_software_files_are_saved_shown_and_downloadable(monkeypatch):
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    monkeypatch.setenv("AYVEN_USE_QWEN_AGENT", "0")
    objective = "Write a small Python program that adds 2 and 3 and prints the sum."

    def gen(role, system, user, max_tokens):
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: the files are the deliverable", 3, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 4}
        if role == "SUPERVISOR":
            return "ACCEPT\nNothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 4}
        if "FILE: main.py" in user:
            return _ESSAY, 40, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 11}
        return "Nothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 2}

    set_role_generator(gen)
    try:
        parent = run_objective(_project(objective), objective, "task-" + uuid.uuid4().hex[:8])
    finally:
        set_role_generator(None)

    conn = connect()
    row = dict(conn.execute("SELECT findings, workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone())
    calls = [
        dict(item) for item in conn.execute(
            "SELECT response_text, finish_reason, prompt_tokens, completion_tokens FROM model_calls WHERE package_id IN (SELECT id FROM work_packages WHERE id=? OR parent_id=?)",
            (parent, parent),
        ).fetchall()
    ]
    conn.close()
    assert any("However" in (item["response_text"] or "") for item in calls)
    assert any(item["finish_reason"] == "stop" and item["prompt_tokens"] == 11 for item in calls)
    assert "However" not in row["findings"]
    assert "print(2+3)" in row["findings"]
    assert "tests: passed" in row["findings"].lower()
    assert row["workflow_state"] == "AWAITING_APPROVAL"
    log_transition(parent, "APPROVED", "test approval")
    from app.intelligence.execution import resume_approved_package

    assert resume_approved_package(parent) == "COMPLETED"

    client = TestClient(app)
    downloaded = client.get(f"/work-packages/{parent}/deliverable.md")
    assert downloaded.status_code == 200
    assert "print(2+3)" in downloaded.text
    assert "tests: passed" in downloaded.text.lower()
    assert "attachment" in downloaded.headers.get("content-disposition", "")
    view = client.get("/campus/view", params={"package_id": parent}).json()
    assert "print(2+3)" in (view.get("result") or {}).get("findings", "")
    assert "print(2+3)" in (view.get("result") or {}).get("deliverable", "")
    page = client.get("/r3f/campus-app.jsx")
    assert page.status_code == 200
    assert "download-deliverable" in page.text


def _door_section(user: str) -> str:
    if "three lines" in user:
        return (
            "Headline: Replacement kitchen doors and worktops\n"
            "Body: The service replaces kitchen doors, worktops and handles.\n"
            "Call to action: Ask for a visit and a written scope."
        )
    if "who pays" in user:
        return "The customer is a household that wants the kitchen doors, worktops and handles replaced."
    if "problem this request" in user:
        return "The problem is that a full kitchen replacement costs more than replacing the doors, worktops and handles."
    if "what is sold" in user:
        return "The offer is replacement of kitchen doors, worktops and handles, which differs from a full kitchen replacement."
    if "two opened pages" in user:
        return "Opened pages state the worktops and handles at https://doors.example/replace and no second price was stated."
    if "Start with Assumption" in user:
        return "Assumption: the opened page did not state a durable price, so the doors, worktops and handles are counted on a visit."
    if "how customers" in user:
        return "Customers are reached through local search and a conversation about replacing kitchen doors, worktops and handles."
    if "asks the customer" in user:
        return "Ask for a visit and a written scope before any booking is made."
    if "first practical" in user:
        return "Next, the owner confirms which kitchens to visit and counts the doors, worktops and handles."
    if "still has to be confirmed" in user:
        return "The plan assumes the request named replacement of kitchen doors, worktops and handles, and a visit still has to see them."
    if "does not settle" in user:
        return "Still open: the towns to cover and the condition of the doors, worktops and handles."
    return "The service replaces kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement."


def test_plan_sections_are_stored_and_the_campus_view_returns_them(monkeypatch):
    from app.intelligence.deliverable import plan_sections_filled

    def search(query, limit=8):
        return [{"title": "Replacement kitchen doors", "url": "https://doors.example/replace", "snippet": "replacement kitchen doors worktops and handles", "rerank_score": 0.8}]

    def fetch(url):
        return {"url": url, "title": "Replacement kitchen doors", "text": "We replace kitchen doors, worktops and handles across the UK.", "error": ""}

    monkeypatch.setattr("app.intelligence.research._live_search", search)
    monkeypatch.setattr("app.intelligence.research._open_live", fetch)
    monkeypatch.setattr("app.intelligence.research.search_provider", lambda query, limit=8: search(query, limit))
    monkeypatch.setattr("app.intelligence.research.fetch_provider", fetch)
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AYVEN_RESEARCH_MODE", "live")
    monkeypatch.setenv("AYVEN_USE_QWEN_AGENT", "0")

    def gen(role, system, user, max_tokens):
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: publish this request only", 3, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 5}
        if role == "SUPERVISOR":
            return "ACCEPT\nNothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 5}
        if "web search queries" in user.lower():
            return "- replacement kitchen doors worktops handles UK prices\n", 4, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 6}
        if any(phrase in user for phrase in ("Write one", "Write two", "Write exactly", "Output only")):
            if "Previous answer" in user:
                return "Okay, the user wants a sentence. " + _door_section(user), 20, {"backend": "generator", "finish_reason": "length", "prompt_tokens": 9}
            return "Okay, the user wants me to write a section of a launch plan.", 12, {"backend": "generator", "finish_reason": "length", "prompt_tokens": 8}
        return "Nothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 2}

    set_role_generator(gen)
    try:
        parent = run_objective(_project(DOOR), DOOR, "task-doors")
    finally:
        set_role_generator(None)

    conn = connect()
    row = dict(conn.execute("SELECT findings FROM work_packages WHERE id=?", (parent,)).fetchone())
    calls = [
        dict(item) for item in conn.execute(
            "SELECT response_text, finish_reason FROM model_calls WHERE package_id IN (SELECT id FROM work_packages WHERE id=? OR parent_id=?)",
            (parent, parent),
        ).fetchall()
    ]
    conn.close()
    assert any("Okay, the user wants" in (item["response_text"] or "") for item in calls)
    assert any(item["finish_reason"] == "length" for item in calls)
    assert "replaces kitchen doors, worktops and handles" in (row["findings"] or "").lower()
    assert plan_sections_filled(row["findings"] or "")
    client = TestClient(app)
    downloaded = client.get(f"/work-packages/{parent}/deliverable.md")
    assert downloaded.status_code == 200
    assert "worktops" in downloaded.text.lower()
    view = client.get("/campus/view", params={"package_id": parent}).json()
    shown = (view.get("result") or {}).get("findings") or ""
    assert "worktops" in shown.lower()
    assert shown == (row["findings"] or "").strip() or "worktops" in ((view.get("result") or {}).get("deliverable") or "").lower()
