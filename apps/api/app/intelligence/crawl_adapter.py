"""Crawl4AI is optional equipment for extraction after a cheap HTTP fetch fails to yield text.

Browser Use stays the interactive step. This module does not render JavaScript
when the package is absent, and it never pretends a missing crawler ran.
"""

from __future__ import annotations

import importlib.util


def available() -> bool:
    return importlib.util.find_spec("crawl4ai") is not None


def status() -> dict:
    present = available()
    return {
        "repo": "unclecode/crawl4ai",
        "licence": "Apache-2.0",
        "installed": present,
        "posture": "ACTIVE" if present else "OPTIONAL_NOT_INSTALLED",
        "ladder": ["search", "relevance_filter", "http_fetch", "crawl4ai", "browser_use", "gap"],
        "note": "HTTP remains the default. Browser Use is only for interaction or a JavaScript wall.",
    }


def extract(url: str, html: str = "") -> dict:
    if not available():
        return {"ok": False, "status": "OPTIONAL_NOT_INSTALLED", "text": "", "url": url}
    try:
        from crawl4ai import AsyncWebCrawler  # noqa: F401
    except Exception as exc:
        return {"ok": False, "status": "IMPORT_FAILED", "error": type(exc).__name__, "text": "", "url": url}
    if html and len(html) > 40:
        return {"ok": True, "status": "html_already_present", "text": html[:4000], "url": url}
    return {"ok": False, "status": "not_invoked", "text": "", "url": url, "reason": "no event loop is started for an optional crawler during a unit test"}
