from __future__ import annotations

import re
from html import unescape

import httpx


def web_search(query: str, limit: int = 5) -> list[dict]:
    """DuckDuckGo HTML search — real network I/O, no API key."""
    try:
        r = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "AyvenCampus/0.1 (research agent)"},
            timeout=20,
            follow_redirects=True,
        )
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        return [{"title": "search_error", "url": "", "snippet": str(exc)}]

    results = []
    for m in re.finditer(
        r'uddg=([^"&]+).*?class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
        html,
        re.I | re.S,
    ):
        url = unescape(m.group(1))
        title = re.sub("<.*?>", "", unescape(m.group(2)))
        snippet = re.sub("<.*?>", "", unescape(m.group(3)))
        results.append({"title": title.strip(), "url": url, "snippet": snippet.strip()[:280]})
        if len(results) >= limit:
            break
    if not results:
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.I | re.S)
        for t in titles[:limit]:
            results.append({"title": re.sub("<.*?>", "", unescape(t)).strip(), "url": "", "snippet": ""})
    return results or [{"title": "no_results", "url": "", "snippet": query}]
