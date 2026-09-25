"""Prospects are extracted from opened pages. Names are not hardcoded."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_FACILITY = re.compile(
    r"\b(leisure centres?|swimming|gym|hospital|railway station|\bstation\b|university|sport|pool|toilets|facilities)\b",
    re.I,
)
_GENERIC = {"home", "search", "welcome", "index"}


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def organisation_name(title: str, url: str) -> str:
    parts = re.split(r"\s+[|\-–—]\s+", title or "")
    for part in parts:
        cleaned = part.strip()
        if cleaned and cleaned.lower() not in _GENERIC:
            return cleaned
    return (title or "").strip() or _host(url) or "Unnamed organisation"


def extract_prospects(evidence: list[dict]) -> list[dict]:
    found = []
    seen: set[str] = set()
    for item in evidence:
        meta = item.get("metadata") or {}
        if meta.get("relevant") is False:
            continue
        url = item.get("source_url") or ""
        host = _host(url)
        if not url:
            continue
        if host in seen:
            continue
        title = item.get("source_title") or ""
        excerpt = re.sub(r"\s+", " ", item.get("extracted_content") or "").strip()
        blob = f"{title} {excerpt}"
        if not _FACILITY.search(blob):
            continue
        if len(excerpt) < 40:
            continue
        org = organisation_name(title, url)
        seen.add(host)
        found.append({
            "organisation": org,
            "url": url,
            "excerpt": excerpt[:360],
            "fact": f"{org} is documented at {url}. The opened page says: {excerpt[:320]}",
            "source_rank": meta.get("source_rank") or "UNKNOWN",
            "freshness": meta.get("freshness") or "UNKNOWN",
        })
    return found
