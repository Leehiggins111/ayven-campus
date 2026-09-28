"""Crawl4AI sits behind Ayven's fetch ladder.

``extract`` is the HTML-to-markdown generator. It does not open a browser.
``crawl_live`` is AsyncWebCrawler (headless browser) for a page whose text is
not in the static HTML. If that browser cannot start, ``route_fetch`` sends
a JavaScript wall to Browser Use and leaves a gap when neither path can run.
LiteLLM is a transitive dependency and is not Ayven's model gateway.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlparse


def available() -> bool:
    return importlib.util.find_spec("crawl4ai") is not None


def live_importable() -> bool:
    if not available():
        return False
    try:
        from crawl4ai import AsyncWebCrawler  # noqa: F401
    except Exception:
        return False
    return True


def status() -> dict:
    present = available()
    return {
        "repo": "unclecode/crawl4ai",
        "licence": "Apache-2.0",
        "installed": present,
        "posture": "ACTIVE" if present else "OPTIONAL_NOT_INSTALLED",
        "ladder": ["search", "relevance_filter", "http_fetch", "crawl4ai_markdown", "crawl4ai_live", "browser_use", "gap"],
        "engine": "crawl4ai.DefaultMarkdownGenerator" if present else "",
        "live_crawl": "WIRED" if live_importable() else "UNAVAILABLE",
        "note": "extract() is HTML to markdown. crawl_live() is AsyncWebCrawler. Browser Use is the fallback when the live crawler cannot start. LiteLLM is not Ayven's model gateway.",
    }


def route_fetch(*, html: str = "", javascript_wall: bool = False, live_available: bool | None = None) -> str:
    """Choose the production fetch step. Markdown extraction is not a live crawl."""
    live = live_importable() if live_available is None else live_available
    if javascript_wall and live:
        return "crawl4ai_live"
    if javascript_wall:
        return "browser_use"
    if html:
        return "crawl4ai_markdown"
    return "gap"


def crawl_live(url: str) -> dict:
    """Headless Crawl4AI fetch. A failure is not reported as a successful crawl."""
    if not live_importable():
        return {"ok": False, "status": "LIVE_UNAVAILABLE", "text": "", "url": url, "engine": ""}
    import asyncio

    async def _run() -> str:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
        from .browser_adapter import chrome_path

        exe = chrome_path() or ""
        config = None
        if "google-chrome" in exe or exe.startswith("/opt/google/"):
            config = BrowserConfig(headless=True, chrome_channel="chrome", verbose=False)
        async with AsyncWebCrawler(config=config) as crawler:
            result = await crawler.arun(url=url)
        markdown = getattr(result, "markdown", None)
        if hasattr(markdown, "raw_markdown"):
            return markdown.raw_markdown or ""
        return str(markdown or "")

    try:
        text = asyncio.run(_run())
    except Exception as exc:
        return {"ok": False, "status": "LIVE_FAILED", "error": f"{type(exc).__name__}: {exc}", "text": "", "url": url, "engine": "crawl4ai.AsyncWebCrawler"}
    text = (text or "")[:8000]
    return {
        "ok": bool(text.strip()),
        "status": "crawled" if text.strip() else "empty",
        "text": text,
        "url": url,
        "engine": "crawl4ai.AsyncWebCrawler",
    }


def extract(url: str, html: str = "") -> dict:
    if not available():
        return {"ok": False, "status": "OPTIONAL_NOT_INSTALLED", "text": "", "url": url}
    body = html or ""
    if not body and url.startswith("file://"):
        path = Path(urlparse(url).path)
        if not path.is_file():
            return {"ok": False, "status": "missing_file", "text": "", "url": url}
        body = path.read_text(encoding="utf-8", errors="replace")
    if not body:
        return {"ok": False, "status": "no_html", "text": "", "url": url, "reason": "nothing to extract"}
    try:
        from crawl4ai import DefaultMarkdownGenerator

        result = DefaultMarkdownGenerator().generate_markdown(input_html=body, base_url=url or "")
        text = (getattr(result, "raw_markdown", None) or "")[:8000]
    except Exception as exc:
        return {"ok": False, "status": "IMPORT_FAILED", "error": f"{type(exc).__name__}: {exc}", "text": "", "url": url}
    return {
        "ok": bool(text.strip()),
        "status": "extracted",
        "text": text,
        "url": url,
        "engine": "crawl4ai.DefaultMarkdownGenerator",
    }
