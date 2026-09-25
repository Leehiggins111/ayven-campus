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

    def _clean(raw: str) -> str:
        return re.sub(r"\s+", " ", re.sub("<.*?>", "", unescape(raw or ""))).strip()

    def _url(raw: str) -> str:
        url = unescape(raw or "").strip()
        if "uddg=" in url:
            match = re.search(r"uddg=([^&]+)", url)
            url = unquote(match.group(1)) if match else url
        if url.startswith("//"):
            url = "https:" + url
        return url

    results = []
    pattern = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
        re.I | re.S,
    )
    legacy = re.compile(
        r'uddg=([^"&]+).*?class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
        re.I | re.S,
    )
    matches = list(pattern.finditer(html)) or list(legacy.finditer(html))
    for match in matches:
        url = _url(match.group(1))
        results.append({"title": _clean(match.group(2)), "url": url, "snippet": _clean(match.group(3))[:400]})
        if len(results) >= limit:
            break
    if not results:
        for match in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            results.append({"title": _clean(match.group(2)), "url": _url(match.group(1)), "snippet": ""})
            if len(results) >= limit:
                break
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
    links = []
    for match in re.finditer(r'href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        href = unescape(match.group(1)).strip()
        anchor = re.sub("<.*?>", " ", unescape(match.group(2)))
        anchor = re.sub(r"\s+", " ", anchor).strip()
        if href.startswith("//"):
            href = f"{parsed.scheme}:{href}"
        elif href.startswith("/"):
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        host = urlparse(href).netloc.lower()
        if host and host != parsed.netloc.lower():
            continue
        if href.startswith("http"):
            links.append({"url": href.split("#")[0], "anchor": anchor[:80]})
        if len(links) >= 6:
            break
    return {
        "url": str(r.url),
        "title": title[:180],
        "text": text[:max_chars],
        "error": "",
        "links": links,
        "requested_url": url,
    }
