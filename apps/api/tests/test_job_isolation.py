"""Two business jobs in one process must not share evidence, queries, or prompts."""

import json
import uuid

from app.db import connect
from app.intelligence.deliverable import page_is_relevant, plan_sections_filled, reject_search_query
from app.intelligence.execution import run_objective
from app.intelligence.memory import remember
from app.models import set_role_generator


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


DOOR = (
    "Prepare a UK business launch plan for a service that replaces kitchen doors, worktops and handles. "
    "A full kitchen replacement is the expensive alternative. "
    "Painting and wrapping are other services, not this service."
)
COFFEE = (
    "Prepare a UK business briefing for a coffee machine rental company. "
    "Cover the service, the customers, the competitors, and pricing."
)


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


def _coffee_section(user: str) -> str:
    if "three lines" in user:
        return (
            "Headline: Coffee machine rental for offices\n"
            "Body: The service is coffee machine rental for the working day.\n"
            "Call to action: Ask for a visit and a written scope."
        )
    if "who pays" in user:
        return "The customer is an office that rents a coffee machine rather than buying one outright."
    if "problem this request" in user:
        return "The problem is that buying a coffee machine costs more than renting one for a small office."
    if "what is sold" in user:
        return "The offer is coffee machine rental, which differs from selling a machine outright to the office."
    if "two opened pages" in user:
        return "Opened pages state the coffee machine rental terms at https://brew.example/rental and no second price was stated."
    if "Start with Assumption" in user:
        return "Assumption: the opened page did not state a durable coffee machine rental price, so a visit still has to see the office."
    if "how customers" in user:
        return "Customers are reached through local search and a conversation about coffee machine rental."
    if "asks the customer" in user:
        return "Ask for a visit and a written scope before any booking is made."
    if "first practical" in user:
        return "Next, the owner confirms which offices to visit and counts the coffee machines to rent."
    if "still has to be confirmed" in user:
        return "The plan assumes the request named coffee machine rental, and a visit still has to see the office."
    if "does not settle" in user:
        return "Still open: which offices want coffee machine rental, and what each visit must count."
    return "The service is coffee machine rental for offices that want machines supplied and maintained."


def test_a_foreign_trade_is_not_relevant_to_another_objective():
    coffee = "Prepare a UK business briefing for a coffee machine rental company."
    assert reject_search_query("kitchen cabinet painting UK prices", coffee) == "off_brief"
    assert reject_search_query("kitchen respray company prices", coffee) == "off_brief"
    assert page_is_relevant(coffee, "Kitchen cabinet painting", "We paint and respray kitchen cabinets.", "https://paint.example/kitchen") is False
    door = "Prepare a UK launch plan for a service that replaces kitchen doors, worktops and handles."
    assert page_is_relevant(door, "Kitchen cabinet painting", "We paint and respray kitchen cabinets.", "https://paint.example/kitchen") is False
    assert page_is_relevant(door, "Replacement kitchen doors", "We replace kitchen doors, worktops and handles.", "https://doors.example/replace") is True
    assert page_is_relevant(coffee, "Office coffee machine rental", "Coffee machine rental for UK offices.", "https://brew.example/rental") is True


def test_research_budget_starts_after_planning(monkeypatch):
    import app.intelligence.research as research_mod

    clock = {"now": 0.0}
    monkeypatch.setattr(research_mod.time, "monotonic", lambda: clock["now"])

    def plan(*_args, **_kwargs):
        clock["now"] = 400.0
        return ["coffee machine rental"], "model"

    monkeypatch.setattr(research_mod, "plan_queries", plan)
    monkeypatch.setenv("AYVEN_MAX_RESEARCH_SECONDS", "30")
    opened = []

    def search(_query, limit=8):
        return [{"title": "Coffee machine rental", "url": "https://brew.example/rental", "snippet": "coffee machine rental"}]

    def fetch(url):
        opened.append(clock["now"])
        return {"url": url, "title": "Coffee machine rental", "text": "Coffee machine rental for offices in the UK.", "error": ""}

    result = research_mod.research(
        "business_research",
        "A coffee machine rental business in the UK.",
        "pkg-budget",
        "research-e3",
        max_rounds=1,
        search_fn=search,
        fetch_fn=fetch,
    )
    assert opened and opened[0] >= 400
    assert result["budget"]["hit"] != "time"
    assert result["evidence"]


def test_door_job_then_coffee_job_share_nothing(monkeypatch):
    prompts = []

    def search(query, limit=8):
        lowered = query.lower()
        if "coffee" in lowered or "rental" in lowered:
            return [{"title": "Office coffee machine rental", "url": "https://brew.example/rental", "snippet": "coffee machine rental for offices", "rerank_score": 0.8}]
        return [{"title": "Replacement kitchen doors", "url": "https://doors.example/replace", "snippet": "replacement kitchen doors worktops and handles", "rerank_score": 0.8}]

    def fetch(url):
        if "brew.example" in url:
            return {"url": url, "title": "Office coffee machine rental", "text": "Coffee machine rental for UK offices. The rental is agreed after a visit.", "error": ""}
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
        prompts.append({"role": role, "system": system, "user": user})
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: publish this request only", 3, {"backend": "generator"}
        if role == "SUPERVISOR":
            return "ACCEPT\nNothing was sent.", 2, {"backend": "generator"}
        if "web search queries" in user.lower():
            if "coffee" in user.lower():
                return "- coffee machine rental UK prices\n", 4, {"backend": "generator"}
            return "- replacement kitchen doors worktops handles UK prices\n", 4, {"backend": "generator"}
        if "coffee" in user.lower() and any(phrase in user for phrase in ("Write one", "Write two", "Write exactly", "Output only")):
            return _coffee_section(user), 20, {"backend": "generator"}
        if any(phrase in user for phrase in ("Write one", "Write two", "Write exactly", "Output only")):
            return _door_section(user), 20, {"backend": "generator"}
        return "Nothing was sent.", 2, {"backend": "generator"}

    door_project = _project(DOOR)
    remember(
        "DOMAIN",
        "business-research",
        "kitchen cabinet painting https://paint.example/kitchen-cabinet-painting",
        provenance="package:earlier",
        tags="business_research",
    )
    remember(
        "PROJECT",
        door_project,
        "Opened https://doors.example/replace for kitchen doors, worktops and handles.",
        provenance="package:door",
        tags="business_research",
    )
    set_role_generator(gen)
    try:
        door_id = run_objective(door_project, DOOR, "task-doors")
        coffee_project = _project(COFFEE)
        coffee_id = run_objective(coffee_project, COFFEE, "task-coffee")
    finally:
        set_role_generator(None)

    conn = connect()
    door = dict(conn.execute("SELECT findings, observability_json FROM work_packages WHERE id=?", (door_id,)).fetchone())
    coffee = dict(conn.execute("SELECT findings, observability_json FROM work_packages WHERE id=?", (coffee_id,)).fetchone())
    door_sources = [row["url"] for row in conn.execute("SELECT url FROM sources WHERE package_id=?", (door_id,)).fetchall()]
    coffee_sources = [row["url"] for row in conn.execute("SELECT url FROM sources WHERE package_id=?", (coffee_id,)).fetchall()]
    conn.close()
    door_obs = json.loads(door["observability_json"])
    coffee_obs = json.loads(coffee["observability_json"])
    door_text = (door["findings"] or "").lower()
    coffee_text = (coffee["findings"] or "").lower()
    coffee_prompts = [item["user"] for item in prompts if "coffee machine" in item["user"].lower()]

    assert "https://doors.example/replace" in door_sources
    assert "worktop" in door_text
    assert "replaces kitchen doors" in door_text
    assert plan_sections_filled(door["findings"] or "")
    assert plan_sections_filled(coffee["findings"] or "")
    assert "https://brew.example/rental" not in door_text
    assert all("brew.example" not in url for url in door_sources)
    assert "https://brew.example/rental" in coffee_sources
    assert "coffee" in coffee_text
    for banned in ("kitchen", "cabinet", "paint", "respray", "worktop", "door", "handle", "alba", "doors.example", "paint.example"):
        assert banned not in coffee_text, banned
        assert all(banned not in url for url in coffee_sources)
        assert all(banned not in " ".join(coffee_obs.get("queries") or []).lower() for banned in (banned,))
    assert "paint.example" not in "\n".join(coffee_prompts)
    assert "worktop" not in "\n".join(coffee_prompts).lower()
    assert "doors.example" not in "\n".join(coffee_prompts)
    assert any("door" in query.lower() or "worktop" in query.lower() for query in door_obs.get("queries") or [])
    assert coffee_obs.get("queries")
    assert all("coffee" in query.lower() or "rental" in query.lower() for query in coffee_obs.get("queries") or [])
