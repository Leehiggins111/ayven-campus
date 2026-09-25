"""Evidence-first web research.

Snippets are leads. A page that can be opened is the evidence. Failures are
recorded. Model memory is not a substitute for a failed fetch.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .toolkit import ToolResult, invoke, now, valid_http_url

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pages.json"

OFFICIAL_HOSTS = (
    "bvb.de",
    "ajax.nl",
    "sparta.cz",
    "rbk.no",
    "gov.uk",
    "service.gov.uk",
    "companieshouse.gov.uk",
)
COMMUNITY_HOSTS = ("reddit.com", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "youtube.com")
HIGH_QUALITY = ("wikipedia.org", "bbc.co.uk", "bbc.com", "theguardian.com")
AVAILABILITY_PHRASES = (
    "on sale",
    "tickets available",
    "in stock",
    "buy now",
    "sold out",
    "vstupenky jsou v prodeji",
    "kjøp kampbilletter",
)


def research_mode() -> str:
    mode = os.environ.get("AYVEN_RESEARCH_MODE", "auto").lower()
    if mode in ("live", "fixtures"):
        return mode
    if os.environ.get("AYVEN_LLM_STUB", "1") != "0":
        return "fixtures"
    return "live"


def rank_source(url: str) -> str:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "UNKNOWN"
    for official in OFFICIAL_HOSTS:
        if host == official or host.endswith("." + official):
            return "PRIMARY_OFFICIAL"
    if any(host == item or host.endswith("." + item) for item in COMMUNITY_HOSTS):
        return "COMMUNITY"
    if any(item in host for item in HIGH_QUALITY):
        return "HIGH_QUALITY_SECONDARY"
    return "OTHER_SECONDARY"


def load_fixtures() -> list[dict]:
    if not _FIXTURES.exists():
        return []
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def queries_for(task_class: str, objective: str) -> list[str]:
    text = objective.lower()
    if task_class == "football_tickets":
        queries = []
        named = (
            ("borussia" in text or "dortmund" in text, "Borussia Dortmund official tickets"),
            ("ajax" in text, "AFC Ajax official kaartverkoop"),
            ("sparta" in text, "AC Sparta Praha official website"),
            ("rosenborg" in text, "Rosenborg BK official billetter"),
        )
        for present, query in named:
            if present:
                queries.append(query)
        if not queries:
            queries.append("official football club ticket sales versus unofficial resale")
        return queries
    if task_class == "vending_prospects":
        return [
            "UK Contracts Finder vending machine notices",
            "Companies House register is not a vending prospect list",
        ]
    if task_class == "internal_door_quote":
        return ["UK internal door trade supplier Scotland delivery"]
    if task_class in ("trivial", "calculation"):
        return []
    return [objective.strip()[:160]]


def _fixture_search(query: str, limit: int) -> list[dict]:
    needle = query.lower()
    hits = []
    for page in load_fixtures():
        if page.get("error"):
            continue
        terms = [term.lower() for term in page.get("match") or []]
        if not any(term in needle for term in terms):
            continue
        hits.append({"title": page.get("title") or "", "url": page["url"], "snippet": (page.get("text") or "")[:240], "fixture": True})
        if len(hits) >= limit:
            break
    return hits


def _live_search(query: str, limit: int) -> list[dict]:
    from ..tools import web_search

    return web_search(query, limit=limit)


def _open_fixture(url: str) -> dict:
    for page in load_fixtures():
        if page.get("url") == url:
            if page.get("error"):
                return {"url": url, "title": page.get("title") or "", "text": "", "error": page["error"]}
            return {
                "url": url,
                "title": page.get("title") or "",
                "text": page.get("text") or "",
                "error": "",
                "freshness": page.get("freshness") or "FIXTURE_SNAPSHOT",
                "retrieved_at": page.get("retrieved_at") or "",
            }
    return {"url": url, "title": "", "text": "", "error": "not_in_fixtures"}


def _open_live(url: str) -> dict:
    from ..tools import fetch_page

    page = fetch_page(url)
    page["freshness"] = "LIVE" if not page.get("error") else "UNKNOWN"
    page["retrieved_at"] = now()
    return page


_SIGNALS = ("zwarthandel", "niet-offici", "unofficial", "reseller", "ticketshop", "billet", "kaart", "offici", "official", "ticket")


def _relevant(text: str, query: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    tokens = [tok for tok in re.findall(r"[a-z0-9]{4,}", query.lower())]
    kept = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(tok in lowered for tok in tokens) or any(signal in lowered for signal in _SIGNALS):
            kept.append(sentence.strip())
        if len(kept) >= 6:
            break
    if kept:
        return " ".join(kept)[:1600]
    return (text or "")[:800]


def _js_wall(text: str) -> bool:
    lowered = (text or "").lower()
    warning = "javascript is disabled" in lowered or "javascript ist deaktiviert" in lowered or "please enable js" in lowered
    if not warning:
        return False
    useful = len(lowered) > 400 and any(word in lowered for word in ("ticket", "kaart", "billet", "vending", "contract", "shop"))
    return not useful


def _channel(url: str, text: str) -> str:
    rank = rank_source(url)
    if rank == "PRIMARY_OFFICIAL":
        return "official"
    lowered = (text or "").lower()
    if any(word in lowered for word in ("ticket", "reseller", "resale", "zwarthandel")):
        return "possible_reseller_not_authorised"
    return "unspecified"


def research(task_class: str, objective: str, package_id: str, agent_id: str, *, project_id: str | None = None, max_rounds: int | None = None) -> dict:
    mode = research_mode()
    rounds = max_rounds if max_rounds is not None else int(os.environ.get("AYVEN_MAX_RESEARCH_ROUNDS", "2"))
    queries = queries_for(task_class, objective)
    evidence: list[dict] = []
    failures: list[dict] = []
    seen: set[str] = set()
    searched: list[str] = []
    if task_class in ("trivial", "calculation") or not queries:
        return {"mode": mode, "queries": [], "evidence": [], "failures": [], "skipped": True}

    search_fn = _fixture_search if mode == "fixtures" else _live_search
    open_fn = _open_fixture if mode == "fixtures" else _open_live
    # Every planned query runs. Extra rounds are only for gaps, so four clubs
    # are not cut off by a two-round budget.
    pending = [(query, 1) for query in queries]
    while pending:
        query, depth = pending.pop(0)
        if query in searched or depth > rounds:
            continue
        searched.append(query)

        captured: dict = {}

        def _search(q=query):
            try:
                hits = search_fn(q, limit=5)
            except Exception as exc:
                captured["hits"] = []
                return ToolResult(tool="web_search", status="error", query=q, error=str(exc)[:300], timestamp=now(), metadata={"mode": mode, "round": depth})
            captured["hits"] = hits
            if not hits or (len(hits) == 1 and (hits[0].get("title") in ("search_error", "no_results"))):
                return ToolResult(tool="web_search", status="empty", query=q, error=(hits[0].get("snippet") if hits else "no_results") or "no_results", timestamp=now(), metadata={"mode": mode, "evidence_level": "none"})
            return ToolResult(
                tool="web_search",
                status="ok",
                query=q,
                timestamp=now(),
                extracted_content="\n".join(f"{h.get('title')} {h.get('url')}" for h in hits)[:1500],
                metadata={"mode": mode, "evidence_level": "snippet", "hit_count": len(hits), "round": depth},
            )

        search_result = invoke(agent_id, "web_search", package_id, _search)
        _emit_tool(project_id, agent_id, "web_search", f"Searching: {query[:120]}")
        if search_result.status != "ok":
            failures.append({"query": query, "stage": "search", "error": search_result.error or search_result.status})
            continue
        hits = captured.get("hits") or []
        opened_any = False
        for hit in hits:
            url = hit.get("url") or ""
            if url in seen or not valid_http_url(url):
                if url and not valid_http_url(url):
                    failures.append({"query": query, "stage": "url", "error": "malformed_or_rejected_url", "url": url})
                continue
            seen.add(url)

            def _fetch(u=url, h=hit):
                page = open_fn(u)
                if page.get("error"):
                    return ToolResult(tool="fetch_page", status="error", query=query, source_url=u, source_title=h.get("title") or "", error=str(page.get("error"))[:300], timestamp=now(), metadata={"mode": mode})
                text = page.get("text") or ""
                if _js_wall(text):
                    return ToolResult(tool="fetch_page", status="error", query=query, source_url=page.get("url") or u, source_title=page.get("title") or "", error="js_wall", extracted_content="", timestamp=page.get("retrieved_at") or now(), metadata={"mode": mode, "evidence_level": "unusable"})
                extract = _relevant(text, query)
                return ToolResult(
                    tool="fetch_page",
                    status="ok",
                    query=query,
                    source_url=page.get("url") or u,
                    source_title=page.get("title") or h.get("title") or "",
                    timestamp=page.get("retrieved_at") or now(),
                    extracted_content=extract,
                    metadata={
                        "mode": mode,
                        "evidence_level": "page",
                        "source_rank": rank_source(page.get("url") or u),
                        "freshness": page.get("freshness") or ("FIXTURE_SNAPSHOT" if mode == "fixtures" else "LIVE"),
                        "channel": _channel(page.get("url") or u, extract),
                        "snippet_was_not_final_evidence": True,
                    },
                )

            fetched = invoke(agent_id, "fetch_page", package_id, _fetch)
            _emit_tool(project_id, agent_id, "fetch_page", f"Opening: {(hit.get('title') or url)[:120]}")
            if fetched.status != "ok":
                failures.append({"query": query, "stage": "fetch", "url": url, "error": fetched.error or fetched.status})
                continue
            opened_any = True
            evidence.append(fetched.as_dict())
        if not opened_any and depth < rounds:
            pending.append((query + " primary official source", depth + 1))
    # Clubs or topics with no opened page stay explicit gaps.
    gaps = []
    blob = " ".join(item.get("extracted_content", "") + item.get("source_url", "") for item in evidence).lower()
    if task_class == "football_tickets":
        for label, needles in (
            ("Borussia Dortmund", ("dortmund", "bvb.de")),
            ("Ajax", ("ajax",)),
            ("Sparta Prague", ("sparta",)),
            ("Rosenborg", ("rosenborg", "rbk.no")),
        ):
            if any(item in objective.lower() for item in needles) and not any(item in blob for item in needles):
                gaps.append(f"No opened page established an official route for {label}.")
    if task_class == "vending_prospects" and not any("vending" in (item.get("extracted_content") or "").lower() and "prospect" in (item.get("extracted_content") or "").lower() for item in evidence):
        gaps.append("No opened page named a vending placement prospect.")
    if task_class == "internal_door_quote" and not evidence:
        gaps.append("No supplier page was retrieved. No supplier is named.")
    return {"mode": mode, "queries": searched, "evidence": evidence, "failures": failures, "gaps": gaps, "skipped": False, "rounds": rounds}


def _emit_tool(project_id: str | None, agent_id: str, tool: str, summary: str) -> None:
    if not project_id:
        return
    from .. import events

    events.emit("agent.using_tool", project_id=project_id, agent_id=agent_id, department_id="research", status="researching", tool=tool, summary=summary, progress=0.4)
    events.emit("agent.researching", project_id=project_id, agent_id=agent_id, department_id="research", status="researching", tool=tool, summary=summary, progress=0.45)


def is_availability_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in AVAILABILITY_PHRASES)
