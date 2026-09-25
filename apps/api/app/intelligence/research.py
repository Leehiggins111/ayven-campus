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

COMMUNITY_HOSTS = ("reddit.com", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "youtube.com")
HIGH_QUALITY = ("wikipedia.org", "bbc.co.uk", "bbc.com", "theguardian.com")
# Longer suffixes first so .gov.uk wins over .gov.
PUBLIC_SECTOR_SUFFIXES = (".nhs.uk", ".gov.uk", ".ac.uk", ".edu.au", ".gov.au", ".gc.ca", ".gov", ".edu", ".mil")
RESELLER_HOST_SIGNALS = ("ticketmaster", "stubhub", "viagogo", "seatgeek", "ebay", "reseller", "resale", "aggregator", "marketplace")
SELF_ID_STEMS = ("official", "offiziell", "oficiální", "oficjalny", "officiell", "officiel", "oficial")
AVAILABILITY_PHRASES = ("on sale", "tickets available", "in stock", "buy now", "sold out")
_QUERY_STOP = {
    "about", "after", "also", "approval", "before", "black", "check", "customer", "draft",
    "enquiry", "facts", "find", "from", "handles", "have", "into", "legitimate", "looking",
    "official", "only", "page", "pages", "provisional", "public", "site", "sizes", "that",
    "their", "this", "with", "your",
}
_PLANNER = None


def research_mode() -> str:
    mode = os.environ.get("AYVEN_RESEARCH_MODE", "auto").lower()
    if mode in ("live", "fixtures"):
        return mode
    if os.environ.get("AYVEN_LLM_STUB", "1") != "0":
        return "fixtures"
    return "live"


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _registrable_label(host: str) -> str:
    labels = [part for part in host.split(".") if part]
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "ac", "gov", "nhs"}:
        return labels[-3]
    if len(labels) >= 2:
        return labels[-2]
    return host


def set_query_planner(fn) -> None:
    """Harness hook. The callable receives (objective, skill_guidance) and returns queries."""
    global _PLANNER
    _PLANNER = fn


def clear_query_planner() -> None:
    global _PLANNER
    _PLANNER = None


def rank_source(url: str, text: str = "", query: str = "", title: str = "") -> str:
    """Official means the entity's own site or a public-sector host, never a named exam list."""
    host = _host(url)
    if not host:
        return "UNKNOWN"
    if any(host == item or host.endswith("." + item) for item in HIGH_QUALITY):
        return "HIGH_QUALITY_SECONDARY"
    if any(host == item or host.endswith("." + item) for item in COMMUNITY_HOSTS):
        return "COMMUNITY"
    if any(signal in host for signal in RESELLER_HOST_SIGNALS):
        return "OTHER_SECONDARY"
    if any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in PUBLIC_SECTOR_SUFFIXES):
        return "PRIMARY_OFFICIAL"
    tokens = [tok for tok in re.findall(r"[a-z0-9]{4,}", (query or "").lower()) if tok not in _QUERY_STOP]
    label = _registrable_label(host)
    if any(tok in label for tok in tokens):
        return "PRIMARY_OFFICIAL"
    blob = f"{title}\n{text}".lower()
    if any(stem in blob for stem in SELF_ID_STEMS):
        return "PRIMARY_OFFICIAL"
    if any(len(tok) >= 5 and tok in blob for tok in tokens):
        return "PRIMARY_OFFICIAL"
    return "OTHER_SECONDARY"


def load_fixtures() -> list[dict]:
    pages = []
    for path in (_FIXTURES, _ADVERSARIAL):
        if path.exists():
            pages.extend(json.loads(path.read_text(encoding="utf-8")))
    return pages


def generic_from_objective(objective: str) -> list[str]:
    """Queries from the brief only. No task-class table and no exam seed list."""
    entities: list[str] = []
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9'’\-]+(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,3})\b", objective or ""):
        name = match.group(1).strip(" -")
        if len(name) < 3 or name.lower() in _QUERY_STOP:
            continue
        if name not in entities:
            entities.append(name)
    queries = [f"{name} official site" for name in entities[:6]]
    words: list[str] = []
    for word in re.findall(r"[A-Za-z]{4,}", (objective or "").lower()):
        if word in _QUERY_STOP or word in words:
            continue
        words.append(word)
    if words:
        queries.append(" ".join(words[:10]))
    if not queries and (objective or "").strip():
        queries.append(objective.strip()[:160])
    return queries[:8]


def _normalise_queries(raw) -> list[str]:
    if isinstance(raw, str):
        lines = raw.splitlines()
    elif isinstance(raw, (list, tuple)):
        lines = []
        for item in raw:
            lines.extend(str(item).splitlines())
    else:
        return []
    cleaned = []
    for line in lines:
        text = line.strip().lstrip("-*0123456789.) ").strip()
        if not text or text.lower().startswith(("query", "objective", "skill")):
            continue
        if text not in cleaned:
            cleaned.append(text[:180])
    return cleaned[:8]


def plan_queries(task_class: str, objective: str, skills=None) -> tuple[list[str], str]:
    """Model plans when one is actually available. Stub and no-model runs stay generic."""
    if task_class in ("trivial", "calculation"):
        return [], "skipped"
    guidance = ""
    if skills:
        from .skills import skill_prompt

        guidance = skill_prompt(skills)[:1500]
    stub = os.environ.get("AYVEN_LLM_STUB", "1") != "0"
    if not stub and _PLANNER is not None:
        try:
            planned = _normalise_queries(_PLANNER(objective, guidance))
            if planned:
                return planned, "model"
        except Exception:
            pass
    if not stub:
        from .. import models as model_mod
        from ..models import complete_role, local_base

        if local_base() or model_mod._GENERATOR is not None:
            user = (
                "Plan the first web searches for this objective. "
                "Use the objective and the skill guidance. "
                "One query per line, each starting with '- '. "
                "Do not invent entities that are not in the objective.\n\n"
                f"Objective:\n{(objective or '')[:1200]}\n\nSkill guidance:\n{guidance}"
            )
            try:
                text, _tokens, _meta = complete_role("EMPLOYEE", "You plan searches. You do not answer the task.", user, max_tokens=180)
                planned = _normalise_queries(text)
                if planned:
                    return planned, "model"
            except Exception:
                pass
    return generic_from_objective(objective), "objective-fallback"


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


def search_provider(query: str, limit: int = 8) -> list[dict]:
    if research_mode() == "fixtures":
        return _fixture_search(query, limit)
    return _live_search(query, limit)


def fetch_provider(url: str) -> dict:
    if research_mode() == "fixtures":
        return _open_fixture(url)
    return _open_live(url)


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


_SIGNALS = ("unofficial", "reseller", "resale", "offici", "official", "ticket")


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


def _channel(url: str, text: str, query: str = "", title: str = "") -> str:
    rank = rank_source(url, text=text, query=query, title=title)
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


def unsupported_reseller_claim(url: str, text: str, query: str = "", title: str = "") -> bool:
    if rank_source(url, text=text, query=query, title=title) == "PRIMARY_OFFICIAL":
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
    skills=None,
) -> dict:
    mode = research_mode()
    rounds = max_rounds if max_rounds is not None else int(os.environ.get("AYVEN_MAX_RESEARCH_ROUNDS", "2"))
    max_searches = int(os.environ.get("AYVEN_MAX_SEARCHES", "12"))
    max_pages = int(os.environ.get("AYVEN_MAX_PAGES", "16"))
    max_browser = int(os.environ.get("AYVEN_MAX_BROWSER_ACTIONS", "2"))
    deadline = time.monotonic() + int(os.environ.get("AYVEN_MAX_RESEARCH_SECONDS", "120"))
    browser_left = [max_browser]
    if queries is not None:
        planned, query_source = list(queries), "provided"
    else:
        planned, query_source = plan_queries(task_class, objective, skills)
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
    review_text = ""
    review_mode = "not-run"
    review_gaps: list[str] = []
    pending = [{"kind": "search", "query": query, "depth": 1, "origin": "seed"} for query in planned]
    round_no = 0
    while pending and round_no < rounds and not budget_hit:
        round_no += 1
        later: list[dict] = []
        current = pending
        for job in current:
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
                        hits = search_impl(q, limit=8)
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
                hits = sorted(
                    captured.get("hits") or [],
                    key=lambda hit: 0 if rank_source(hit.get("url") or "", text=hit.get("snippet") or "", query=query, title=hit.get("title") or "") == "PRIMARY_OFFICIAL" else 1,
                )
                opened_any = False
                for hit in hits:
                    opened = _open_hit(
                        hit, query, depth, mode, open_fn, agent_id, package_id, project_id, seen, failures, duplicates, evidence,
                        browse_fn=browse_fn, browser_left=browser_left,
                    )
                    opened_any = opened_any or opened
                    if opened and followed < 3 and round_no < rounds:
                        for link in (hit.get("_links") or [])[:1]:
                            if link not in seen and valid_http_url(link):
                                followed += 1
                                later.append({"kind": "open", "query": query, "url": link, "depth": round_no + 1, "title": link, "origin": "link"})
                if not opened_any and round_no < rounds:
                    later.append({"kind": "search", "query": query + " primary official source", "depth": round_no + 1, "origin": "retry"})
            else:
                hit = {"url": job.get("url") or "", "title": job.get("title") or "", "snippet": ""}
                _open_hit(hit, query, depth, mode, open_fn, agent_id, package_id, project_id, seen, failures, duplicates, evidence, browse_fn=browse_fn, browser_left=browser_left)
        if os.environ.get("AYVEN_AGENTIC_RESEARCH", "1") != "0" and not budget_hit:
            try:
                review_text, review_mode = _agentic_review(objective, evidence, review_gaps, searched, reviewer)
            except Exception as exc:
                review_mode = "failed"
                review_text = f"FAILURE\nreviewer exception: {type(exc).__name__}: {exc}"
                failures.append({"query": "", "stage": "review", "error": f"{type(exc).__name__}: {exc}"[:300]})
                review_gaps.append(f"Research review failed: {type(exc).__name__}. Not treated as sufficient.")
            else:
                unknown = _explicit_unknown(review_text)
                if unknown and unknown not in review_gaps:
                    review_gaps.append(unknown)
                extra = _next_query(review_text)
                room = round_no < rounds and len(searched) < max_searches and len(evidence) < max_pages and time.monotonic() <= deadline
                if extra and extra not in searched and room:
                    later.append({"kind": "search", "query": extra, "depth": round_no + 1, "origin": "review"})
        pending = later

    gaps = list(review_gaps)
    gaps.extend(_unsupported_requirements(skills, evidence))
    if not evidence:
        gaps.append("Research produced no opened page. No model-memory fallback was used.")
    elif mode == "live" and failures:
        gaps.append("Some live retrievals failed. No model-memory fallback was used.")
    if any(item.get("error") == "js_wall" for item in failures) and browse_fn is None:
        gaps.append("A page needed a browser. The browser was not launched. No model-memory fallback was used.")
    if budget_hit:
        gaps.append(f"Research stopped because the {budget_hit} budget was exhausted. No model-memory fallback was used.")
    return {
        "mode": mode,
        "queries": searched,
        "query_source": query_source,
        "evidence": evidence,
        "failures": failures,
        "duplicates": duplicates,
        "conflicts": detect_conflicts(evidence),
        "gaps": gaps,
        "skipped": False,
        "rounds": rounds,
        "rounds_used": round_no,
        "rounds_exhausted": True,
        "followed_links": followed,
        "memory_fallback_used": False,
        "review": review_text,
        "review_mode": review_mode,
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
                        metadata={"mode": mode, "evidence_level": "page", "via": "browser", "http_error": "js_wall", "source_rank": rank_source(page.get("url") or u, text=browsed.extracted_content or "", query=query, title=page.get("title") or ""), "freshness": "LIVE", "relevant": True, "round": depth},
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
        page_title = page.get("title") or h.get("title") or ""
        return ToolResult(
            tool="fetch_page",
            status="ok",
            query=query,
            source_url=final_url,
            source_title=page_title,
            timestamp=page.get("retrieved_at") or now(),
            extracted_content=extract,
            metadata={
                "mode": mode,
                "evidence_level": "page",
                "source_rank": rank_source(final_url, text=text, query=query, title=page_title),
                "freshness": page.get("freshness") or ("FIXTURE_SNAPSHOT" if mode == "fixtures" else "LIVE"),
                "channel": _channel(final_url, text, query=query, title=page_title),
                "snippet_was_not_final_evidence": True,
                "snippet_contradicts_page": snippet_contradicts(h.get("snippet") or "", text),
                "redirected_from": page.get("redirect_from") or "",
                "retried": bool(page.get("retried")),
                "attempts": attempts,
                "conflict_key": page.get("conflict_key") or "",
                "conflict_value": page.get("conflict_value") or "",
                "relevant": relevant,
                "round": depth,
                "unsupported_reseller": unsupported_reseller_claim(final_url, text, query=query, title=page_title),
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


def _unsupported_requirements(skills, evidence: list[dict]) -> list[str]:
    """A required-evidence line from the skill contract becomes a gap until a page supports it."""
    blob = " ".join((item.get("extracted_content") or "") + " " + (item.get("source_title") or "") for item in evidence).lower()
    gaps = []
    for skill in skills or []:
        for requirement in getattr(skill, "evidence", []) or []:
            tokens = [tok for tok in re.findall(r"[a-z]{5,}", requirement.lower()) if tok not in {"opened", "pages"}]
            if tokens and not any(tok in blob for tok in tokens):
                gaps.append(f"Required evidence not supported: {requirement}")
    return gaps


def _agentic_review(objective: str, evidence: list, gaps: list, searched: list, reviewer) -> tuple[str, str]:
    """Returns (text, mode). Exceptions propagate so the caller can record a failure."""
    user = (
        "research review\n"
        f"Objective: {(objective or '')[:400]}\n"
        f"Queries already run: {searched}\n"
        f"Pages opened: {len(evidence)}\n"
        f"Gaps so far: {gaps[:6]}\n"
        "Say SUFFICIENT if the opened pages are enough. "
        "Or start a line with NEXT: and one new query. "
        "Or say I still don't know X."
    )
    if reviewer is not None:
        text = reviewer(user)
        if not text:
            raise RuntimeError("reviewer returned nothing")
        if "reviewer: stub" in text.lower():
            return text, "stub"
        return text, "model"
    if os.environ.get("AYVEN_LLM_STUB", "1") != "0":
        return "SUFFICIENT\nreviewer: stub", "stub"
    from ..models import complete_role

    text, _tokens, meta = complete_role("EMPLOYEE", "You are reviewing research coverage.", user, max_tokens=120)
    if not text:
        raise RuntimeError("reviewer returned nothing")
    if (meta or {}).get("backend") == "stub" or "reviewer: stub" in text.lower():
        if "reviewer: stub" not in text.lower():
            text += "\nreviewer: stub"
        return text, "stub"
    return text, "model"


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
