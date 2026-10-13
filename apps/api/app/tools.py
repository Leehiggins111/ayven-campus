from __future__ import annotations

import re
from html import unescape
from urllib.parse import unquote, urlparse

import httpx

UA = "AyvenCampus/0.3 (research agent; +https://github.com/Leehiggins111/ayven-campus)"


def combined_search(query: str, limit: int = 6) -> list[dict]:
    """Backward-compatible name. New research goes through the intelligence engine."""
    return web_search(query, limit=limit)


def _clean_html(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub("<.*?>", "", unescape(raw or ""))).strip()


def _search_url(raw: str) -> str:
    url = unescape(raw or "").strip()
    if "uddg=" in url:
        match = re.search(r"uddg=([^&]+)", url)
        url = unquote(match.group(1)) if match else url
    if url.startswith("//"):
        url = "https:" + url
    return url


def parse_search_html(html: str, limit: int = 6) -> list[dict]:
    """Links from a DuckDuckGo HTML results page."""
    results = []
    pattern = re.compile(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
        re.I | re.S,
    )
    legacy = re.compile(
        r'uddg=([^"&]+).*?class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</',
        re.I | re.S,
    )
    matches = list(pattern.finditer(html or "")) or list(legacy.finditer(html or ""))
    for match in matches:
        results.append({
            "title": _clean_html(match.group(2)),
            "url": _search_url(match.group(1)),
            "snippet": _clean_html(match.group(3))[:400],
        })
        if len(results) >= limit:
            return results
    if not results:
        for match in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html or "", re.I | re.S):
            results.append({"title": _clean_html(match.group(2)), "url": _search_url(match.group(1)), "snippet": ""})
            if len(results) >= limit:
                break
    return results


def parse_lite_html(html: str, limit: int = 6) -> list[dict]:
    """Links from the DuckDuckGo lite results table."""
    results = []
    pattern = re.compile(
        r'class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result-snippet"[^>]*>(.*?)</',
        re.I | re.S,
    )
    for match in pattern.finditer(html or ""):
        url = _search_url(match.group(1))
        if not url.startswith("http"):
            continue
        results.append({
            "title": _clean_html(match.group(2)),
            "url": url,
            "snippet": _clean_html(match.group(3))[:400],
        })
        if len(results) >= limit:
            break
    return results


def _post_search(url: str, query: str) -> str:
    response = httpx.post(
        url,
        data={"q": query},
        headers={"User-Agent": UA},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text or ""


def web_search(query: str, limit: int = 6) -> list[dict]:
    """DuckDuckGo HTML search — real network I/O, no API key."""
    html = ""
    try:
        html = _post_search("https://html.duckduckgo.com/html/", query)
    except Exception as exc:
        html = ""
        first_error = str(exc)
    else:
        first_error = ""
    results = parse_search_html(html, limit=limit)
    if results:
        return results
    try:
        lite = _post_search("https://lite.duckduckgo.com/lite/", query)
    except Exception as exc:
        if first_error:
            return [{"title": "search_error", "url": "", "snippet": first_error}]
        return [{"title": "search_error", "url": "", "snippet": str(exc)}]
    results = parse_lite_html(lite, limit=limit)
    if results:
        return results
    if first_error and not html:
        return [{"title": "search_error", "url": "", "snippet": first_error}]
    return [{"title": "no_results", "url": "", "snippet": query}]


_CHROME = re.compile(
    r"^(skip to (?:main )?content|top of page|shopping cart(?: \(\d+\))?|log in|sign in|privacy policy|cookie policy|wishlist \(\d+\))$",
    re.I,
)


def extract_main_text(html: str, max_chars: int = 6000) -> str:
    """Keep the article. Navigation and chrome are not the page."""
    raw = html or ""
    text = ""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            raw,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if extracted and len(extracted.strip()) >= 40:
            text = extracted
    except Exception:
        text = ""
    if not text:
        cleaned = re.sub(r"(?is)<(script|style|nav|footer|header|noscript|form)[^>]*>.*?</\1>", " ", raw)
        cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
        cleaned = re.sub(r"(?is)</p>", "\n", cleaned)
        text = re.sub("<.*?>", " ", unescape(cleaned))
    kept = []
    for line in re.split(r"\n+", text):
        piece = re.sub(r"\s+", " ", line).strip()
        if not piece or _CHROME.match(piece):
            continue
        if piece.lower() in {"top of page", "skip to main content", "shopping cart (0)"}:
            continue
        kept.append(piece)
    return "\n".join(kept)[:max_chars].strip()


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
        error = re.sub(r"\s*For more information check:\s*\S+", "", str(exc)).strip()
        return {"url": url, "title": "", "text": "", "error": error[:200]}
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = re.sub("<.*?>", "", unescape(title_m.group(1))).strip() if title_m else parsed.netloc
    text = extract_main_text(html, max_chars=max_chars)
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
