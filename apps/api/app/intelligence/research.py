"""Evidence-first web research.

Snippets are leads. A page that can be opened is the evidence. Failures are
recorded. Model memory is not a substitute for a failed fetch.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .toolkit import ToolResult, invoke, now, valid_http_url

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pages.json"
_ADVERSARIAL = Path(__file__).resolve().parent / "fixtures" / "adversarial.json"

OFFICIAL_HOSTS = (
    "bvb.de",
    "ajax.nl",
    "sparta.cz",
    "rbk.no",
    "gov.uk",
    "service.gov.uk",
    "companieshouse.gov.uk",
    "nhs.uk",
    "nationalrail.co.uk",
    "edinburghleisure.co.uk",
    "ed.ac.uk",
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
    pages = []
    for path in (_FIXTURES, _ADVERSARIAL):
        if path.exists():
            pages.extend(json.loads(path.read_text(encoding="utf-8")))
    return pages


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
            "UK public leisure centre swimming pool and gym",
            "UK railway station passenger facilities",
            "UK NHS hospital address and facilities",
            "UK university sport venues and gym membership",
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
        if page.get("error") and not page.get("url"):
            continue
        terms = [term.lower() for term in page.get("match") or []]
        if not any(term in needle for term in terms):
            continue
        hits.append({
            "title": page.get("title") or "",
            "url": page["url"],
            "snippet": page.get("snippet") or (page.get("text") or "")[:240],
            "fixture": True,
        })
        if len(hits) >= limit:
            break
    return hits


def _live_search(query: str, limit: int) -> list[dict]:
    from ..tools import web_search

    return web_search(query, limit=limit)


def _page_payload(page: dict, redirect_from: str = "") -> dict:
    return {
        "url": page.get("url") or "",
        "title": page.get("title") or "",
        "text": page.get("text") or "",
        "error": page.get("error") or "",
        "freshness": page.get("freshness") or "FIXTURE_SNAPSHOT",
        "retrieved_at": page.get("retrieved_at") or "",
        "redirect_from": redirect_from,
        "links": page.get("links") or [],
        "conflict_key": page.get("conflict_key") or "",
        "conflict_value": page.get("conflict_value") or "",
    }


def _open_fixture(url: str) -> dict:
    pages = load_fixtures()
    page = next((item for item in pages if item.get("url") == url), None)
    if not page:
        return {"url": url, "title": "", "text": "", "error": "not_in_fixtures", "links": []}
    if page.get("error"):
        return _page_payload(page)
    target = page.get("redirect_to")
    if target:
        landed = next((item for item in pages if item.get("url") == target), None)
        if not landed:
            return {"url": url, "title": "", "text": "", "error": "redirect_target_missing", "redirect_from": url, "links": []}
        payload = _page_payload(landed, redirect_from=url)
        return payload
    return _page_payload(page)


def _open_live(url: str) -> dict:
    from ..tools import fetch_page

    page = fetch_page(url)
    page["freshness"] = "LIVE" if not page.get("error") else "UNKNOWN"
    page["retrieved_at"] = now()
    page.setdefault("links", [])
    if page.get("url") and page.get("url") != url:
        page["redirect_from"] = url
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
    warning = (
        "javascript is disabled" in lowered
        or "javascript ist deaktiviert" in lowered
        or "please enable js" in lowered
        or "please enable javascript" in lowered
        or "enable javascript and cookies" in lowered
    )
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


def snippet_contradicts(snippet: str, page: str) -> bool:
    nums = re.findall(r"\d{3,}", snippet or "")
    if any(num not in (page or "") for num in nums):
        return True
    if "available" in (snippet or "").lower() and "available" not in (page or "").lower():
        return True
    return False


def unsupported_reseller_claim(url: str, text: str) -> bool:
    if rank_source(url) == "PRIMARY_OFFICIAL":
        return False
    lowered = (text or "").lower()
    return "authorised reseller" in lowered or "authorized reseller" in lowered or ("official partner" in lowered and "reseller" in lowered)


def detect_conflicts(evidence: list[dict]) -> list[dict]:
    groups: dict[str, list] = {}
    for item in evidence:
        meta = item.get("metadata") or {}
        key = meta.get("conflict_key") or ""
        if not key:
            continue
        groups.setdefault(key, []).append({"url": item.get("source_url"), "value": meta.get("conflict_value")})
    conflicts = []
    for key, rows in groups.items():
        values = {row["value"] for row in rows}
        if len(values) > 1:
            conflicts.append({"key": key, "values": sorted(values), "sources": rows})
    return conflicts


def _link_relevant(url: str, anchor: str) -> bool:
    blob = f"{url} {anchor}".lower()
    return any(word in blob for word in ("facilit", "ticket", "leisure", "vending", "pool", "gym", "hospital", "station", "billet", "kaart"))


def research(
    task_class: str,
    objective: str,
    package_id: str,
    agent_id: str,
    *,
    project_id: str | None = None,
    max_rounds: int | None = None,
    queries: list[str] | None = None,
    search_fn=None,
    fetch_fn=None,
    reviewer=None,
    browse_fn=None,
) -> dict:
    mode = research_mode()
    rounds = max_rounds if max_rounds is not None else int(os.environ.get("AYVEN_MAX_RESEARCH_ROUNDS", "2"))
    max_searches = int(os.environ.get("AYVEN_MAX_SEARCHES", "12"))
    max_pages = int(os.environ.get("AYVEN_MAX_PAGES", "16"))
    max_browser = int(os.environ.get("AYVEN_MAX_BROWSER_ACTIONS", "2"))
    deadline = time.monotonic() + int(os.environ.get("AYVEN_MAX_RESEARCH_SECONDS", "120"))
    browser_left = [max_browser]
    planned = list(queries) if queries is not None else queries_for(task_class, objective)
    evidence: list[dict] = []
    failures: list[dict] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    searched: list[str] = []
    followed = 0
    if task_class in ("trivial", "calculation") or not planned:
        return {
            "mode": mode, "queries": [], "evidence": [], "failures": [], "duplicates": [],
            "conflicts": [], "skipped": True, "rounds_exhausted": True, "memory_fallback_used": False,
        }

    search_impl = search_fn or (_fixture_search if mode == "fixtures" else _live_search)
    open_fn = fetch_fn or (_open_fixture if mode == "fixtures" else _open_live)
    if browse_fn is None and mode == "live":
        browse_fn = _live_browse
    budget_hit = ""
    # Every planned query runs at depth 1. Later depths are gap reformulations
    # and a bounded number of relevant links, not a reason to drop a club.
    pending = [{"kind": "search", "query": query, "depth": 1} for query in planned]
    while pending:
        if len(searched) >= max_searches:
            budget_hit = "searches"
            break
        if len(evidence) >= max_pages:
            budget_hit = "pages"
            break
        if time.monotonic() > deadline:
            budget_hit = "time"
            break
        if browser_left[0] < 0:
            budget_hit = "browser"
            break
        job = pending.pop(0)
        query = job["query"]
        depth = job["depth"]
        if depth > rounds:
            continue
        if job["kind"] == "search":
            if query in searched:
                continue
            searched.append(query)
            captured: dict = {}

            def _search(q=query, d=depth):
                try:
                    hits = search_impl(q, limit=5)
                except Exception as exc:
                    captured["hits"] = []
                    return ToolResult(tool="web_search", status="error", query=q, error=str(exc)[:300], timestamp=now(), metadata={"mode": mode, "round": d})
                captured["hits"] = hits or []
                if not hits or (len(hits) == 1 and (hits[0].get("title") in ("search_error", "no_results"))):
                    return ToolResult(tool="web_search", status="empty", query=q, error=(hits[0].get("snippet") if hits else "no_results") or "no_results", timestamp=now(), metadata={"mode": mode, "evidence_level": "none"})
                return ToolResult(
                    tool="web_search",
                    status="ok",
                    query=q,
                    timestamp=now(),
                    extracted_content="\n".join(f"{h.get('title')} {h.get('url')}" for h in hits)[:1500],
                    metadata={"mode": mode, "evidence_level": "snippet", "hit_count": len(hits), "round": d},
                )

            search_result = invoke(agent_id, "web_search", package_id, _search)
            _emit_tool(project_id, agent_id, "web_search", f"Searching: {query[:120]}")
            if search_result.status != "ok":
                failures.append({"query": query, "stage": "search", "error": search_result.error or search_result.status})
                continue
            hits = sorted(captured.get("hits") or [], key=lambda hit: 0 if rank_source(hit.get("url") or "") == "PRIMARY_OFFICIAL" else 1)
            opened_any = False
            for hit in hits:
                opened = _open_hit(
                    hit, query, depth, mode, open_fn, agent_id, package_id, project_id, seen, failures, duplicates, evidence,
                    browse_fn=browse_fn, browser_left=browser_left,
                )
                opened_any = opened_any or opened
                if opened and followed < 3 and depth < rounds:
                    for link in (hit.get("_links") or [])[:1]:
                        if link not in seen and valid_http_url(link):
                            followed += 1
                            pending.append({"kind": "open", "query": query, "url": link, "depth": depth + 1, "title": link})
            if not opened_any and depth < rounds:
                pending.append({"kind": "search", "query": query + " primary official source", "depth": depth + 1})
        else:
            hit = {"url": job.get("url") or "", "title": job.get("title") or "", "snippet": ""}
            _open_hit(hit, query, depth, mode, open_fn, agent_id, package_id, project_id, seen, failures, duplicates, evidence, browse_fn=browse_fn, browser_left=browser_left)

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
    if task_class == "vending_prospects":
        from .prospects import extract_prospects

        if not extract_prospects(evidence):
            gaps.append("No opened page established a real organisation that is plausibly relevant to vending placement.")
        else:
            gaps.append("Decision-maker, acceptance, footfall, and existing vending arrangements remain unknown unless a page stated them.")
    if task_class == "internal_door_quote" and not evidence:
        gaps.append("No supplier page was retrieved. No supplier is named.")
    if not evidence:
        gaps.append("Research produced no opened page. No model-memory fallback was used.")
    elif mode == "live" and failures:
        gaps.append("Some live retrievals failed. No model-memory fallback was used.")
    if any(item.get("error") == "js_wall" for item in failures) and browse_fn is None:
        gaps.append("A page needed a browser. The browser was not launched. No model-memory fallback was used.")
    if budget_hit:
        gaps.append(f"Research stopped because the {budget_hit} budget was exhausted. No model-memory fallback was used.")
    review_text = ""
    if os.environ.get("AYVEN_AGENTIC_RESEARCH", "1") != "0":
        review_text = _agentic_review(objective, evidence, gaps, searched, reviewer)
        unknown = _explicit_unknown(review_text)
        if unknown:
            gaps.append(unknown)
        extra = _next_query(review_text)
        if extra and extra not in searched and not budget_hit and len(searched) < max_searches and len(evidence) < max_pages and time.monotonic() <= deadline:
            searched.append(extra)
            try:
                hits = search_impl(extra, limit=5) or []
            except Exception as exc:
                hits = []
                failures.append({"query": extra, "stage": "search", "error": str(exc)[:300]})
            for hit in hits:
                if len(evidence) >= max_pages:
                    break
                _open_hit(hit, extra, rounds, mode, open_fn, agent_id, package_id, project_id, seen, failures, duplicates, evidence, browse_fn=browse_fn, browser_left=browser_left)
    return {
        "mode": mode,
        "queries": searched,
        "evidence": evidence,
        "failures": failures,
        "duplicates": duplicates,
        "conflicts": detect_conflicts(evidence),
        "gaps": gaps,
        "skipped": False,
        "rounds": rounds,
        "rounds_exhausted": True,
        "followed_links": followed,
        "memory_fallback_used": False,
        "review": review_text,
        "budget": {"max_rounds": rounds, "max_searches": max_searches, "max_pages": max_pages, "max_browser_actions": max_browser, "hit": budget_hit},
        "browser_actions": max_browser - browser_left[0],
    }


def _open_hit(hit, query, depth, mode, open_fn, agent_id, package_id, project_id, seen, failures, duplicates, evidence, browse_fn=None, browser_left=None) -> bool:
    url = hit.get("url") or ""
    if url in seen:
        duplicates.append(url)
        return False
    if not valid_http_url(url):
        failures.append({
            "query": query,
            "stage": "url",
            "error": "malformed_or_rejected_url" if url else "empty_result_url",
            "url": url,
        })
        return False
    seen.add(url)

    def _fetch(u=url, h=hit):
        page = open_fn(u)
        attempts = 1
        if page.get("error"):
            retried = open_fn(u)
            attempts = 2
            retried = dict(retried)
            retried["retried"] = True
            page = retried
        if page.get("error"):
            return ToolResult(
                tool="fetch_page", status="error", query=query, source_url=u, source_title=h.get("title") or "",
                error=str(page.get("error"))[:300], timestamp=now(),
                metadata={"mode": mode, "attempts": attempts, "round": depth},
            )
        text = page.get("text") or ""
        if _js_wall(text):
            from .fallbacks import on_http_result

            decision = on_http_result(
                status="error",
                error="js_wall",
                browser_available=browse_fn is not None,
                browser_permitted=browse_fn is not None and (browser_left is None or browser_left[0] > 0),
            )
            if decision["launch_browser"]:
                if browser_left is not None:
                    browser_left[0] -= 1
                browsed = browse_fn(agent_id, package_id, page.get("url") or u)
                if getattr(browsed, "status", "") == "ok" and (browsed.extracted_content or ""):
                    return ToolResult(
                        tool="browser",
                        status="ok",
                        query=query,
                        source_url=browsed.source_url or page.get("url") or u,
                        source_title=page.get("title") or "",
                        timestamp=now(),
                        extracted_content=browsed.extracted_content,
                        metadata={"mode": mode, "evidence_level": "page", "via": "browser", "http_error": "js_wall", "source_rank": rank_source(page.get("url") or u), "freshness": "LIVE", "relevant": True, "round": depth},
                    )
            return ToolResult(
                tool="fetch_page", status="error", query=query, source_url=page.get("url") or u,
                source_title=page.get("title") or "", error="js_wall", extracted_content="",
                timestamp=page.get("retrieved_at") or now(),
                metadata={"mode": mode, "evidence_level": "unusable", "attempts": attempts, "js_only": True, "browser": decision["action"]},
            )
        extract = _relevant(text, query)
        tokens = re.findall(r"[a-z0-9]{4,}", query.lower())
        relevant = any(tok in (extract + " " + (page.get("title") or "")).lower() for tok in tokens) if tokens else True
        h["_links"] = [
            link.get("url") for link in (page.get("links") or [])
            if isinstance(link, dict) and _link_relevant(link.get("url") or "", link.get("anchor") or "")
        ]
        final_url = page.get("url") or u
        return ToolResult(
            tool="fetch_page",
            status="ok",
            query=query,
            source_url=final_url,
            source_title=page.get("title") or h.get("title") or "",
            timestamp=page.get("retrieved_at") or now(),
            extracted_content=extract,
            metadata={
                "mode": mode,
                "evidence_level": "page",
                "source_rank": rank_source(final_url),
                "freshness": page.get("freshness") or ("FIXTURE_SNAPSHOT" if mode == "fixtures" else "LIVE"),
                "channel": _channel(final_url, extract),
                "snippet_was_not_final_evidence": True,
                "snippet_contradicts_page": snippet_contradicts(h.get("snippet") or "", text),
                "redirected_from": page.get("redirect_from") or "",
                "retried": bool(page.get("retried")),
                "attempts": attempts,
                "conflict_key": page.get("conflict_key") or "",
                "conflict_value": page.get("conflict_value") or "",
                "relevant": relevant,
                "round": depth,
                "unsupported_reseller": unsupported_reseller_claim(final_url, text),
            },
        )

    fetched = invoke(agent_id, "fetch_page", package_id, _fetch)
    _emit_tool(project_id, agent_id, "fetch_page", f"Opening: {(hit.get('title') or url)[:120]}")
    if fetched.status != "ok":
        failures.append({"query": query, "stage": "fetch", "url": url, "error": fetched.error or fetched.status, "attempts": (fetched.metadata or {}).get("attempts")})
        return False
    evidence.append(fetched.as_dict())
    return True


def _emit_tool(project_id: str | None, agent_id: str, tool: str, summary: str) -> None:
    if not project_id:
        return
    from .. import events

    events.emit("agent.using_tool", project_id=project_id, agent_id=agent_id, department_id="research", status="researching", tool=tool, summary=summary, progress=0.4)
    events.emit("agent.researching", project_id=project_id, agent_id=agent_id, department_id="research", status="researching", tool=tool, summary=summary, progress=0.45)


def _live_browse(agent_id: str, package_id: str, url: str):
    from .browser_adapter import available, open_page

    if not available():
        return None
    return open_page(agent_id, package_id, url)


def _agentic_review(objective: str, evidence: list, gaps: list, searched: list, reviewer) -> str:
    user = (
        "research review\n"
        f"Objective: {(objective or '')[:400]}\n"
        f"Queries already run: {searched}\n"
        f"Pages opened: {len(evidence)}\n"
        f"Gaps so far: {gaps[:6]}\n"
        "Say SUFFICIENT if the opened pages are enough. "
        "Or start with NEXT: and one new query. "
        "Or say I still don't know X."
    )
    try:
        if reviewer is not None:
            return reviewer(user) or "SUFFICIENT"
        from ..models import complete_role

        text, _tokens, _meta = complete_role("EMPLOYEE", "You are reviewing research coverage.", user, max_tokens=120)
        return text or "SUFFICIENT"
    except Exception:
        return "SUFFICIENT"


def _explicit_unknown(text: str) -> str:
    for line in (text or "").splitlines():
        if "i still don't know" in line.lower():
            return line.strip()
    return ""


def _next_query(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("NEXT:"):
            return stripped.split(":", 1)[1].strip()[:180]
    return ""


def is_availability_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in AVAILABILITY_PHRASES)
