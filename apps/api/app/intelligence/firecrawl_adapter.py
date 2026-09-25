"""Firecrawl stays behind an API boundary. Its source is AGPL-3.0 and is not vendored.

This build does not call Firecrawl. Set nothing; the adapter reports disabled.
"""

from __future__ import annotations

import os

from .toolkit import ToolResult, now


def status() -> dict:
    return {
        "repo": "firecrawl/firecrawl",
        "licence": "AGPL-3.0",
        "selected": False,
        "enabled": False,
        "reason": "AGPL code is not copied. No API call is made in this build.",
        "key_present": bool(os.environ.get("AYVEN_FIRECRAWL_API_KEY")),
    }


def scrape(url: str) -> ToolResult:
    return ToolResult(
        tool="firecrawl",
        status="disabled",
        query=url,
        source_url=url,
        error="Firecrawl adapter is off. AGPL source is not vendored and no request is sent.",
        timestamp=now(),
        metadata=status(),
    )
