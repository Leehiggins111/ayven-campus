"""Regression tests rebuilt from the preserved v1.1.1 GPU archive.

Fixture text may name the exam. Engine modules may not.
"""

import json
import uuid
from pathlib import Path

from app.db import connect
from app.intelligence.audit import challenge_material_claims
from app.intelligence.boundary import is_noise_hit, queries_from_plan_text, reject_reasoning_query
from app.intelligence.claims import add_claim
from app.intelligence.grounding import ground_text
from app.intelligence.repair import apply_repairs
from app.intelligence.research import _plain_text, rank_source, research

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gpu_archive" / "contaminated_queries.json"


def _archive() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _package() -> str:
    package = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO work_packages(id,project_id,title,objective,origin,stage,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (package, "p", "t", "o", "milo", "employee", "drafting", "t", "t"),
    )
    conn.commit()
    conn.close()
    return package


def test_archive_think_queries_and_media_cannot_execute():
    archive = _archive()
    executed = []
    fetched = []

    def search(query, limit=5):
        executed.append(query)
        return [
            {"title": "think.mp3", "url": "https://cdn.example.net/infinitive/think.mp3", "snippet": "audio"},
            {"title": "Dictionary", "url": "https://dictionary.example.net/dictionary/english/think", "snippet": "verb"},
        ]

    def fetch(url):
        fetched.append(url)
        return {"url": url, "title": "page", "text": "plain notice", "error": ""}

    for row in archive["planner_outputs"]:
        raw = row["query"]
        assert reject_reasoning_query(raw), raw[:80]
        kept = queries_from_plan_text(raw)
        research(
            "web_research",
            "Find the named organisation.",
            _package(),
            "research-e1",
            queries=kept or [raw],
            max_rounds=1,
            search_fn=search,
            fetch_fn=fetch,
        )
    direct = research(
        "web_research",
        "Find the named organisation.",
        _package(),
        "research-e1",
        queries=["<think>", "think.mp3"],
        max_rounds=1,
        search_fn=search,
        fetch_fn=fetch,
    )
    blob = json.dumps({"executed": executed, "fetched": fetched, "direct": direct})
    assert "<think" not in blob
    assert "think.mp3" not in blob
    assert "dictionary/english/think" not in blob
    assert all(reject_reasoning_query(query) == "" for query in executed)
    assert direct["queries"] == []
    for url in archive["noise_urls"]:
        path = url.lower()
        if "think.mp3" in path or "/dictionary/" in path or "/thesaurus/" in path or path.endswith(".css") or "/wiki/think" in path or path.rstrip("/").endswith("/think"):
            assert is_noise_hit(url), url
    sample = archive["route_claim"]["text"]
    assert "think.mp3" not in _plain_text(sample)
    assert "<link" not in _plain_text(sample).lower()
    assert rank_source(
        "https://shop.example.net/",
        text="Official site and online store for snacks",
        title="Official Site",
        query="think",
    ) != "PRIMARY_OFFICIAL"


def test_archive_route_is_rejected_researched_rewritten_and_rechecked():
    archive = _archive()
    snippet = archive["route_claim"]
    package = _package()
    claim = add_claim(
        package,
        "research-e1",
        snippet["text"],
        "ROUTE",
        evidence_text="opened page",
        source_url=snippet["url"],
        source_type="PRIMARY_OFFICIAL",
    )
    rows = challenge_material_claims([claim], "opened page", objective=archive["objective_for_route_claim"])
    assert rows[0]["result"] == "DISPROVED"
    follow = rows[0]["follow_up"]
    assert follow and reject_reasoning_query(follow) == ""
    seen = []

    def search(query, limit=5):
        seen.append(query)
        return [{"title": follow, "url": "https://notice.example.net/entity", "snippet": follow}]

    def fetch(url):
        return {"url": url, "title": follow, "text": f"{follow} published notice", "error": ""}

    section = snippet["text"]
    report = apply_repairs(
        package,
        rows,
        objective=archive["objective_for_route_claim"],
        section=section,
        search_fn=search,
        fetch_fn=fetch,
    )
    assert seen == [follow]
    assert report["retries"] > 0
    action = report["actions"][0]
    assert action["action"] == "RESEARCH_MORE"
    assert action["resolved"] is True
    assert action["recheck"] != "DISPROVED"
    assert follow.split()[0].lower() in report["section"].lower()
    assert "stylesheet" not in report["section"].lower()
    conn = connect()
    status = conn.execute("SELECT status, claim_text, source_url FROM claims WHERE id=?", (claim["id"],)).fetchone()
    conn.close()
    assert status["status"] == "SUPPORTED"
    assert status["source_url"].endswith("/entity")
    assert "stylesheet" not in (status["claim_text"] or "").lower()


def test_archive_route_stays_unresolved_when_research_finds_nothing():
    archive = _archive()
    snippet = archive["route_claim"]
    package = _package()
    claim = add_claim(
        package,
        "research-e1",
        snippet["text"],
        "ROUTE",
        evidence_text="opened page",
        source_url=snippet["url"],
        source_type="UNKNOWN",
    )
    rows = challenge_material_claims([claim], "opened page", objective=archive["objective_for_route_claim"])
    seen = []

    def search(query, limit=5):
        seen.append(query)
        return []

    report = apply_repairs(
        package,
        rows,
        objective=archive["objective_for_route_claim"],
        section=snippet["text"],
        search_fn=search,
        fetch_fn=lambda url: {"url": url, "text": "", "error": "empty"},
    )
    assert seen
    assert report["actions"][0]["action"] == "UNRESOLVED_GAP"
    assert report["actions"][0]["resolved"] is False
    assert "no supporting page" in report["actions"][0]["detail"].lower()


def test_source_failure_is_a_gap_not_a_contradiction():
    package = _package()
    claim = add_claim(
        package,
        "research-e1",
        "Retrieval failed at fetch: Client error for url https://dictionary.example/dictionary/english/think",
        "SOURCE_FAILURE",
        evidence_text='{"stage":"fetch"}',
        source_url="https://dictionary.example/dictionary/english/think",
        source_type="UNKNOWN",
    )
    rows = challenge_material_claims([claim], "")
    assert rows[0]["result"] == "STOOD"
    assert "gap" in rows[0]["resolution"].lower()


def test_customer_stated_price_stays_when_it_is_in_the_input():
    stripped = ground_text("Door: £82.", "", "1533.00")
    assert stripped["removed"]
    kept = ground_text("Door: £82.", "", "82.00 18.00 door £82")
    assert not kept["removed"]
    assert "82" in kept["text"]
