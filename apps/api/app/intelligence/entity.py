"""Entity-first research targets. Queries are built from an entity and an information need."""

from __future__ import annotations

import re

_STOP = {
    "about", "after", "also", "before", "check", "draft", "find", "from", "have", "into",
    "looking", "only", "page", "pages", "public", "that", "their", "this", "with", "your",
    "what", "when", "where", "which", "would", "could", "should", "please", "research",
    "legitimate", "facts", "official", "enquiry", "approval", "customer",
}

_PUBLIC = ("government", "council", "ministry", "regulator", "hospital", "university", "nhs", "municipal")
_OFFICIAL = ("official", "ticket", "price", "supplier", "manufacturer", "policy")


def extract_entities(objective: str) -> list[dict]:
    names: list[str] = []
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9'’\-]+(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,3})\b", objective or ""):
        name = match.group(1).strip(" -")
        if len(name) < 3 or name.lower() in _STOP:
            continue
        if name not in names:
            names.append(name)
    words = [word for word in re.findall(r"[A-Za-z]{4,}", (objective or "").lower()) if word not in _STOP]
    need = " ".join(words[:8])
    lowered = (objective or "").lower()
    if any(word in lowered for word in _PUBLIC):
        likely = "PRIMARY_PUBLIC_BODY"
    elif any(word in lowered for word in _OFFICIAL):
        likely = "PRIMARY_OFFICIAL"
    else:
        likely = "UNKNOWN"
    targets = []
    for name in names[:6]:
        kind = "organisation" if " " in name else "named_thing"
        targets.append({
            "entity": name,
            "entity_type": kind,
            "requested_information": need,
            "likely_source_type": likely,
        })
    if not targets and need:
        targets.append({
            "entity": " ".join(words[:3]) or "objective",
            "entity_type": "topic",
            "requested_information": need,
            "likely_source_type": likely,
        })
    return targets


def queries_for_targets(targets: list[dict]) -> list[str]:
    queries = []
    for target in targets:
        entity = (target.get("entity") or "").strip()
        need = (target.get("requested_information") or "").strip()
        if not entity:
            continue
        queries.append(f"{entity} {need}".strip()[:180])
        if target.get("likely_source_type") == "PRIMARY_PUBLIC_BODY":
            queries.append(f"{entity} official public record"[:180])
        else:
            queries.append(f"{entity} official source"[:180])
    kept = []
    for query in queries:
        if query and query not in kept:
            kept.append(query)
    return kept[:8]
