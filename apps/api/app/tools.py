from __future__ import annotations

import re
from html import unescape
from urllib.parse import unquote, urlparse

import httpx

UA = "AyvenCampus/0.3 (research agent; +https://github.com/Leehiggins111/ayven-campus)"


def combined_search(query: str, limit: int = 6) -> list[dict]:
    """Backward-compatible name. New research goes through the intelligence engine."""
    return web_search(query, limit=limit)


def web_search(query: str, limit: int = 6) -> list[dict]:
    """DuckDuckGo HTML search — real network I/O, no API key."""
    try:
        r = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": UA},
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
        url = unquote(unescape(m.group(1)))
        title = re.sub("<.*?>", "", unescape(m.group(2)))
        snippet = re.sub("<.*?>", "", unescape(m.group(3)))
        results.append({"title": title.strip(), "url": url, "snippet": snippet.strip()[:400]})
        if len(results) >= limit:
            break
    if not results:
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.I | re.S)
        hrefs = re.findall(r'uddg=([^"&]+)', html)
        for i, t in enumerate(titles[:limit]):
            url = unquote(unescape(hrefs[i])) if i < len(hrefs) else ""
            results.append({"title": re.sub("<.*?>", "", unescape(t)).strip(), "url": url, "snippet": ""})
    return results or [{"title": "no_results", "url": "", "snippet": query}]


def fetch_page(url: str, max_chars: int = 6000) -> dict:
    """Fetch a page and extract readable text."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"url": url, "title": "", "text": "", "error": "invalid_url"}
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=18, follow_redirects=True)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        return {"url": url, "title": "", "text": "", "error": str(exc)[:200]}
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub("<.*?>", "", unescape(title_m.group(1))).strip() if title_m else parsed.netloc
    cleaned = re.sub(r"(?is)<(script|style|nav|footer|noscript)[^>]*>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</p>", "\n", cleaned)
    text = re.sub("<.*?>", " ", unescape(cleaned))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"url": str(r.url), "title": title[:180], "text": text[:max_chars], "error": ""}
