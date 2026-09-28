"""Crawl4AI is rung 4 of the research ladder: markdown extraction after HTTP.

Browser Use stays the interactive step. This module runs Crawl4AI's markdown
generator on HTML that was already fetched, including a local file page.
It does not start a paid browser service.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlparse


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
        "engine": "crawl4ai.DefaultMarkdownGenerator" if present else "",
        "note": "HTTP remains the default. Browser Use is only for interaction or a JavaScript wall. LiteLLM is a transitive dependency of Crawl4AI and is not Ayven's model gateway.",
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
