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
            "Headline: Worn kitchen doors, same room.\n"
            "Body: Households can replace the kitchen doors, worktops and handles they already have without a full kitchen replacement.\n"
            "Call to action: Ask for a visit and a written scope."
        )
    if "Name the customer." in user:
        return "The customer is a household that wants the kitchen doors, worktops and handles replaced."
    if "Name the problem." in user:
        return "The problem is that a full kitchen replacement costs more than replacing the doors, worktops and handles."
    if "Name the offer." in user:
        return "The offer is replacement of kitchen doors, worktops and handles, which differs from a full kitchen replacement."
    if "name one opened page" in user:
        return (
            "Kitchen Door Co offers fitted kitchen doors, worktops and handles. "
            "Refresh Kit offers a kitchen door replacement. "
            "This service differs by replacing the doors, worktops and handles rather than a full replacement."
        )
    if "Do not invent a £ figure" in user:
        return (
            "Assumption: doors — not stated as a durable price, so the kitchen doors are counted on a visit.\n"
            "Assumption: worktops — not stated on an opened page.\n"
            "Assumption: handles — not stated on an opened page."
        )
    if "Name the channel." in user:
        return "Customers are reached through local search in the first week, where the owner posts one note about replacing kitchen doors, worktops and handles."
    if "Ask the reader to get in touch" in user:
        return "Ask for a visit and a written scope before any booking is made."
    if "Name the owner's next action." in user:
        return (
            "In the first week the owner opens two supplier pages and writes down the prices they publish for the doors, worktops and handles. "
            "In the first month the owner visits one kitchen and measures the doors."
        )
    if "Name one assumption." in user:
        return "The plan assumes a visit still has to see the kitchen doors, worktops and handles before any figure is used."
    if "Name one open fact." in user:
        return "Still open: the towns to cover and the condition of the doors, worktops and handles."
    if "one line only" in user:
        return "Headline: Worn kitchen doors, same room."
    return "The service replaces kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement."


def _coffee_section(user: str) -> str:
    if "three lines" in user:
        return (
            "Headline: Coffee machine rental for offices\n"
            "Body: An office can rent a coffee machine for the working day.\n"
            "Call to action: Ask for a visit and a written scope."
        )
    if "Name the customer." in user:
        return "The customer is an office that rents a coffee machine rather than buying one outright."
    if "Name the problem." in user:
        return "The problem is that buying a coffee machine costs more than renting one for a small office."
    if "Name the offer." in user:
        return "The offer is coffee machine rental, which differs from selling a machine outright to the office."
    if "name one opened page" in user:
        return (
            "Office Coffee Co offers coffee machine rental for an office. "
            "Brew Example offers a weekly coffee machine hire. "
            "This service differs by renting the machine rather than selling one."
        )
    if "Do not invent a £ figure" in user:
        return "Assumption: the opened page did not state a durable coffee machine rental price, so a visit still has to see the office."
    if "Name the channel." in user:
        return "Customers are reached through local search in the first week, where the owner posts one note about coffee machine rental."
    if "Ask the reader to get in touch" in user:
        return "Ask for a visit and a written scope before any booking is made."
    if "Name the owner's next action." in user:
        return (
            "In the first week the owner confirms which offices to visit and counts the coffee machines to rent. "
            "In the first month the owner visits one office and sets the rental after that visit."
        )
    if "Name one assumption." in user:
        return "The plan assumes a visit still has to see the office before a coffee machine rental price is used."
    if "Name one open fact." in user:
        return "Still open: which offices want coffee machine rental, and what each visit must count."
    if "one line only" in user:
        return "Headline: Coffee machines for the working day."
    return "The service is coffee machine rental for offices that want machines supplied and maintained."


def test_the_service_sentence_is_the_section_brief_and_a_long_echo_is_not_a_query():
    from app.intelligence.deliverable import instruction_echo, service_brief, targeted_queries, usable_search_queries

    clarified = (
        "Create a business launch plan. "
        "Lee answered: The service replaces kitchen doors, worktops and handles, and it avoids a full kitchen replacement. "
        "Painting and wrapping are other services, not this service. "
        "Do not invent a service area, a home base, prices, testimonials, completed jobs, or guarantees."
    )
    brief = service_brief(clarified)
    assert "replaces kitchen doors" in brief.lower()
    assert "do not invent" not in brief.lower()
    assert "painting and wrapping" not in brief.lower()
    assert instruction_echo(COFFEE)
    assert reject_search_query("Research UK businesses", clarified) in {"off_brief", "instruction_only"}
    assert page_is_relevant(
        clarified,
        "Business innovation",
        "Research and development in the UK carried out or funded by business enterprises.",
        "https://www.ons.gov.uk/business",
    ) is False
    assert usable_search_queries([COFFEE], COFFEE) == []
    assert any("coffee" in query and "rental" in query for query in targeted_queries(COFFEE))


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
            return [
                {"title": "Office Coffee Co", "url": "https://brew.example/rental", "snippet": "coffee machine rental for offices", "rerank_score": 0.8},
                {"title": "Brew Example", "url": "https://hirecoffee.co.uk/hire", "snippet": "coffee machine rental hire", "rerank_score": 0.7},
            ]
        return [
            {"title": "Kitchen Door Co", "url": "https://doors.example/replace", "snippet": "replacement kitchen doors worktops and handles", "rerank_score": 0.8},
            {"title": "Refresh Kit", "url": "https://refreshkit.co.uk/offer", "snippet": "kitchen doors worktops and handles", "rerank_score": 0.7},
        ]

    def fetch(url):
        if "hirecoffee.co.uk" in url:
            return {"url": url, "title": "Brew Example", "text": "Brew Example hires a coffee machine for offices in the UK.", "error": ""}
        if "brew.example" in url:
            return {"url": url, "title": "Office Coffee Co", "text": "Coffee machine rental for UK offices. The rental is agreed after a visit.", "error": ""}
        if "refreshkit.co.uk" in url:
            return {"url": url, "title": "Refresh Kit", "text": "Refresh Kit replaces kitchen doors, worktops and handles.", "error": ""}
        return {"url": url, "title": "Kitchen Door Co", "text": "We replace kitchen doors, worktops and handles across the UK.", "error": ""}

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
    assert plan_sections_filled(door["findings"] or "", "", DOOR)
    assert plan_sections_filled(coffee["findings"] or "", "", COFFEE)
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
