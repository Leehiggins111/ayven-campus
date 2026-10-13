"""Lightweight local reranker. No model download. Scores are stored with the hit."""

from __future__ import annotations

import re

from .authority import classify_authority

_AUTHORITY_BONUS = {
    "PRIMARY_OFFICIAL": 0.25,
    "PRIMARY_PUBLIC_BODY": 0.25,
    "PRIMARY_DOCUMENT": 0.2,
    "REPUTABLE_SECONDARY": 0.12,
    "MARKETPLACE": 0.02,
    "COMMUNITY": 0.0,
    "UNKNOWN": 0.0,
}


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


def score_hit(query: str, hit: dict, objective: str = "") -> float:
    blob = " ".join([
        hit.get("title") or "",
        hit.get("snippet") or "",
        hit.get("url") or "",
    ])
    query_tokens = _tokens(query) | _tokens(objective)
    have = _tokens(blob)
    if not query_tokens:
        overlap = 0.0
    else:
        overlap = len(query_tokens & have) / len(query_tokens)
    authority = classify_authority(hit.get("url") or "", text=hit.get("snippet") or "", title=hit.get("title") or "", query=query)
    bonus = _AUTHORITY_BONUS.get(authority, 0.0)
    penalty = 0.0
    lowered = blob.lower()
    if "think.mp3" in lowered or lowered.endswith(".mp3"):
        penalty = 1.0
    score = max(0.0, min(1.0, overlap + bonus - penalty))
    return round(score, 3)


def rerank(query: str, hits: list[dict], objective: str = "") -> list[dict]:
    scored = []
    for hit in hits:
        row = dict(hit)
        row["rerank_score"] = score_hit(query, row, objective)
        row["authority_class"] = classify_authority(row.get("url") or "", text=row.get("snippet") or "", title=row.get("title") or "", query=query)
        scored.append(row)
    scored.sort(key=lambda item: item["rerank_score"], reverse=True)
    return scored
