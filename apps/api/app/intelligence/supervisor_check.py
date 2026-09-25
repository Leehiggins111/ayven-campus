"""Independent supervisor checks.

The query is built from the claim text. It is not taken from the employee's
search list. A page that was only opened again from the stored URL is labelled
re-read, not independently verified.
"""

from __future__ import annotations

import re

from .claims import evidence_entails

_STOP = {"this", "that", "with", "from", "have", "been", "were", "their", "about", "which", "before", "after"}


def query_from_claim(text: str) -> str:
    words = [word for word in re.findall(r"[A-Za-z0-9]{4,}", text or "") if word.lower() not in _STOP]
    return " ".join(words[:12])[:180] or (text or "")[:120]


def _material(claims: list[dict]) -> list[dict]:
    kept = []
    for claim in claims or []:
        if claim.get("claim_type") in ("MISSING_INFORMATION", "CALCULATION"):
            continue
        if claim.get("status") not in ("SUPPORTED", "PARTIALLY_SUPPORTED"):
            continue
        if (claim.get("claim_text") or "").strip():
            kept.append(claim)
    return kept


def _judge(claim_text: str, page_text: str) -> str:
    if evidence_entails(claim_text, page_text):
        return "independently_confirmed"
    claim_numbers = re.findall(r"\d+(?:\.\d+)?", claim_text or "")
    page_numbers = re.findall(r"\d+(?:\.\d+)?", page_text or "")
    if claim_numbers and any(number not in (page_text or "") for number in claim_numbers) and page_numbers:
        return "independently_contradicted"
    return "independently_unconfirmed"


def independent_verify(
    claims: list[dict],
    *,
    search_fn,
    fetch_fn,
    browse_fn=None,
    browser_allowed: bool = False,
    max_searches: int = 2,
    max_fetches: int = 3,
) -> dict:
    """Up to max_searches and max_fetches for the whole call. Later claims are budget_exhausted."""
    searches = 0
    fetches = 0
    checks = []
    for claim in _material(claims):
        if searches >= max_searches and fetches >= max_fetches:
            checks.append(_row(claim, "budget_exhausted", "", ""))
            continue
        query = query_from_claim(claim.get("claim_text") or "")
        hit = None
        if searches < max_searches:
            searches += 1
            try:
                hits = search_fn(query, limit=3) or []
            except Exception as exc:
                hits = []
                checks.append(_row(claim, "independently_unconfirmed", query, f"search_error:{type(exc).__name__}"))
                continue
            hit = next((item for item in hits if item.get("url")), None)
        page_text = ""
        note = ""
        verdict = ""
        if hit and fetches < max_fetches:
            fetches += 1
            page = _page(fetch_fn, hit.get("url") or "")
            if page.get("error") and browser_allowed and browse_fn is not None:
                browsed = browse_fn(hit.get("url") or "")
                page_text = _browse_text(browsed)
                note = "browser" if page_text else "browser_failed"
            else:
                page_text = page.get("text") or ""
                note = page.get("error") or ""
            verdict = _judge(claim.get("claim_text") or "", page_text) if page_text else "independently_unconfirmed"
            checks.append(_row(claim, verdict, query, note or hit.get("url") or ""))
            continue
        source = claim.get("source_url") or ""
        if source and fetches < max_fetches:
            fetches += 1
            page = _page(fetch_fn, source)
            checks.append(_row(claim, "re_read_only", query, page.get("error") or source))
            continue
        checks.append(_row(claim, "budget_exhausted", query, ""))
    return {"checks": checks, "searches": searches, "fetches": fetches}


def _page(fetch_fn, url: str) -> dict:
    try:
        page = fetch_fn(url) or {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "text": ""}
    if not isinstance(page, dict):
        return {"error": "malformed_page", "text": ""}
    return page


def _browse_text(browsed) -> str:
    if browsed is None:
        return ""
    if isinstance(browsed, dict):
        return browsed.get("text") or browsed.get("extracted_content") or ""
    return getattr(browsed, "extracted_content", "") or ""


def _row(claim: dict, verification: str, query: str, note: str) -> dict:
    return {
        "claim_id": claim.get("id") or "",
        "claim_type": claim.get("claim_type") or "",
        "verification": verification,
        "query": query,
        "note": (note or "")[:240],
    }
