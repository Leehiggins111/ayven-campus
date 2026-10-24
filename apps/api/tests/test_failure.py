"""Degrade when a dependency fails. Model memory is not a substitute."""

import uuid

from app.db import connect
from app.intelligence.execution import run_objective
from app.intelligence.memory import remember
from app.intelligence.research import research
from app.models import set_role_generator


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


def test_model_unavailable_still_publishes_the_ledger():
    def boom(role, system, user, max_tokens):
        raise RuntimeError("model down")

    set_role_generator(boom)
    try:
        parent = run_objective(_project("Say hello"), "Say hello", None)
    finally:
        set_role_generator(None)
    conn = connect()
    row = conn.execute("SELECT findings, observability_json FROM work_packages WHERE id=?", (parent,)).fetchone()
    conn.close()
    assert row["findings"]
    assert "model down" in row["observability_json"] or "unavailable" in row["observability_json"]


def test_search_exception_is_a_gap_not_a_memory():
    def explode(_query, limit=3):
        raise TimeoutError("search timed out")

    result = research("web_research", "Find a notice", "pkg-fail", "research-e3", queries=["notice"], max_rounds=1, search_fn=explode)
    assert result["evidence"] == []
    assert result["memory_fallback_used"] is False
    assert any(item["stage"] == "search" for item in result["failures"])


def test_reasoning_is_not_stored_as_memory():
    assert remember("COMPANY", "co", "<think>secret plan</think>", provenance="test") == ""


def test_invalid_url_and_redirect_loop_do_not_invent_a_page():
    result = research(
        "web_research",
        "Find a notice",
        "pkg-url",
        "research-e3",
        queries=["broken"],
        max_rounds=1,
        search_fn=lambda query, limit=3: [{"title": "bad", "url": "http://bad url", "snippet": "nope"}],
        fetch_fn=lambda url: {"url": url, "text": "", "error": "redirect_loop"},
    )
    assert result["evidence"] == []
    assert result["memory_fallback_used"] is False
