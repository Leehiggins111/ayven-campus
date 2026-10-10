"""Business-plan queries, relevance, and whether a package may be completed.

A page that does not match the objective is not evidence. A section that only
says Unresolved is not a finished plan. Safety can pass while the task fails.
"""

from __future__ import annotations

import re

_PRONOUNS = {
    "i", "me", "my", "mine", "we", "our", "ours", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "they", "them", "their", "theirs",
    "it", "its", "myself", "yourself", "ourselves",
}
_QUERY_BOILERPLATE = {
    "about", "after", "also", "and", "answered", "approval", "ask", "before",
    "builder", "builders", "business", "call", "channel", "channels", "check",
    "copy", "cover", "covers", "covering", "create", "created", "customer",
    "draft", "evidence", "find", "for", "from", "have", "help", "http", "https",
    "include", "into", "item", "items", "launch", "list", "local", "make",
    "market", "name", "next", "official", "offer", "only", "page", "pages",
    "plan", "planned", "prepare", "pricing", "problem", "public", "real",
    "research", "service", "site", "sites", "step", "steps", "that", "the",
    "their", "this", "unresolved", "website", "websites", "webpage", "with",
    "write", "www", "your", "action", "actions", "assumption", "assumptions",
    "competitor", "competitors", "advert", "advertisement",
}
_GENERIC_TERMS = _QUERY_BOILERPLATE | _PRONOUNS | {
    "buyer", "confirm", "current", "does", "dont", "each", "gaps", "gap",
    "given", "hours", "just", "more", "must", "need", "needs", "notice",
    "notices", "opened", "opening", "package", "please", "product", "publish",
    "renewal", "send", "should", "source", "sources", "staff", "still",
    "than", "usual", "want", "what", "when", "where", "which", "wholesale",
    "will", "without", "work", "would",
}
_TRADE_STEMS = ("paint", "respray", "spray", "decorat", "lacquer", "refinish")
_HEADINGS = (
    ("service", ("service", "the service")),
    ("target customer", ("target customer", "the customer", "customer")),
    ("problem", ("problem", "the problem")),
    ("offer and positioning", ("offer and positioning", "offer", "positioning")),
    ("competitor and market research", ("competitor and market research", "competitor research", "competitors", "market research")),
    ("pricing", ("pricing", "pricing assumptions", "price")),
    ("channels", ("channels", "marketing channels")),
    ("advert", ("advert", "ad copy", "advert copy", "advertisement")),
    ("call to action", ("call to action", "cta")),
    ("next steps", ("next steps", "next step")),
    ("assumptions", ("assumptions", "assumption")),
    ("unresolved", ("unresolved", "unresolved items")),
)
_REQUIRED = (
    "service",
    "target customer",
    "problem",
    "offer and positioning",
    "competitor and market research",
    "pricing",
    "channels",
    "advert",
    "call to action",
    "next steps",
)
_BOILERPLATE_BODY = (
    "ask what the work includes and what it would cost",
    "unresolved. no opened page",
    "unresolved as a researched fact",
    "the request asks for a launch plan",
    "facts come from the request",
    "no opened page stated",
    "draft only, using the name in the request",
    "confirm the service and the customer with the owner",
)
_HEADING_LINE = re.compile(r"^\s{0,3}(?:#{1,3}\s*|\d+[.)]\s*|[-*]\s*)?([A-Za-z][A-Za-z0-9 /&'-]{2,80})\s*:?\s*$")


def requester_names(objective: str) -> set[str]:
    """Names the requester used to sign an answer, plus the owner name used in prompts."""
    names = {"lee"}
    for match in re.finditer(r"\b([A-Za-z]+)\s+answered\b", objective or "", re.I):
        names.add(match.group(1).lower())
    return names


def instruction_echo(query: str) -> bool:
    """True when the model repeated the planner's directions instead of a search."""
    lowered = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not lowered:
        return False
    if lowered.startswith((
        "the queries", "each query", "one query", "name the", "shape:", "shape ",
        "do not", "don't", "write ", "return a",
    )):
        return True
    if "do not search" in lowered or "one query per line" in lowered or "starting with" in lowered:
        return True
    return False


def quoted_searches(text: str) -> list[str]:
    found = []
    for match in re.findall(r"['\"]([^'\"]{8,140})['\"]", text or ""):
        item = re.sub(r"\s+", " ", match).strip()
        if item and item not in found:
            found.append(item)
    return found


def reject_search_query(query: str, objective: str = "") -> str:
    """Reject a search built from the requester's name, a pronoun, or instruction words."""
    from .boundary import reject_reasoning_query

    reason = reject_reasoning_query(query)
    if reason:
        return reason
    tokens = re.findall(r"[a-z0-9']+", (query or "").lower())
    if not tokens:
        return "empty"
    if any(token in requester_names(objective) for token in tokens):
        return "requester_name"
    if any(token in _PRONOUNS for token in tokens):
        return "pronoun"
    content = [token for token in tokens if token not in _QUERY_BOILERPLATE and len(token) >= 3]
    if not content:
        return "instruction_only"
    return ""


def usable_search_queries(items: list[str], objective: str = "") -> list[str]:
    """Drop instruction echoes. Keep a quoted search that was buried inside one."""
    kept: list[str] = []
    for item in items:
        if instruction_echo(item) or reject_search_query(item, objective):
            for quote in quoted_searches(item):
                if instruction_echo(quote) or reject_search_query(quote, objective):
                    continue
                if quote not in kept:
                    kept.append(quote[:180])
            continue
        short = (item or "").strip()[:180]
        if short and short not in kept:
            kept.append(short)
    return kept[:8]


def distinctive_terms(objective: str) -> list[str]:
    """Service words left after requester names, pronouns, and instruction words are removed."""
    names = requester_names(objective)
    seen: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9'+-]{2,}", objective or ""):
        token = word.lower().strip("'+-")
        if len(token) < 4 or token in _GENERIC_TERMS or token in names or token in _PRONOUNS:
            continue
        if token not in seen:
            seen.append(token)
    return seen


def region_token(objective: str) -> str:
    if re.search(r"\b(uk|u\.k\.|united kingdom|britain)\b", objective or "", re.I):
        return "UK"
    return ""


def targeted_queries(objective: str) -> list[str]:
    """Searches about the service, the place, and prices. Not the requester."""
    terms = distinctive_terms(objective)
    if not terms:
        return []
    region = region_token(objective)
    price = "prices" if re.search(r"\bpric", objective or "", re.I) else ""
    queries = []
    primary = " ".join(terms[:5] + ([region] if region else []) + ([price] if price else []))
    primary = re.sub(r"\s+", " ", primary).strip()
    if primary and not reject_search_query(primary, objective):
        queries.append(primary[:180])
    trade = [term for term in terms if _trade_stem(term)]
    domain = [term for term in terms if term not in trade][:2]
    if trade:
        shaped = " ".join(domain + trade[:2] + ([region] if region else []) + ([price] if price else []))
        shaped = re.sub(r"\s+", " ", shaped).strip()
        if shaped and shaped not in queries and not reject_search_query(shaped, objective):
            queries.append(shaped[:180])
    return queries


def _trade_stem(term: str) -> str:
    for stem in _TRADE_STEMS:
        if stem in (term or ""):
            return stem
    return ""


def _trade_in_blob(term: str, blob: str) -> bool:
    stem = _trade_stem(term)
    if not stem:
        return False
    if stem == "paint":
        return any(part in blob for part in ("paint", "respray", "spray", "lacquer", "refinish"))
    return stem in blob


def page_is_relevant(objective: str, title: str = "", text: str = "", url: str = "") -> bool:
    """True when the page is about the objective. Off-topic pages are not evidence."""
    terms = distinctive_terms(objective)
    if not terms:
        return True
    blob = f"{title}\n{text}\n{url}".lower()
    trades = [term for term in terms if _trade_stem(term)]
    if trades and not any(_trade_in_blob(term, blob) for term in trades):
        return False
    hits = [term for term in terms if term in blob]
    if trades and hits:
        return True
    if len(hits) >= 2:
        return True
    if len(hits) == 1 and len(hits[0]) >= 5:
        score = _similarity(objective, f"{title}. {(text or '')[:500]}")
        if score is None:
            return True
        return score >= 0.45
    return False


def _similarity(left: str, right: str) -> float | None:
    try:
        from .memory import _cosine, _embed
    except Exception:
        return None
    vectors = _embed([(left or "")[:500], (right or "")[:500]])
    if not vectors or len(vectors) < 2:
        return None
    return _cosine(vectors[0], vectors[1])


def _heading_label(text: str) -> str:
    label = re.sub(r"\s+", " ", (text or "")).strip(" :").lower()
    for canonical, aliases in _HEADINGS:
        if label in aliases:
            return canonical
    return ""


def _split_heading(line: str) -> tuple[str, str]:
    """A heading on its own line, or 'Heading: the sentence' on one line."""
    cleaned = re.sub(r"[*_`]+", "", line or "").strip()
    if not cleaned:
        return "", ""
    match = _HEADING_LINE.match(cleaned)
    if match:
        label = _heading_label(match.group(1))
        if label:
            return label, ""
    head, sep, rest = cleaned.partition(":")
    if not sep:
        return "", ""
    if head.strip().lower() in {"assumption", "fact", "inference", "recommendation", "unknown"}:
        return "", ""
    label = _heading_label(head)
    if label:
        return label, rest.strip()
    return "", ""


def _normalise_heading(line: str) -> str:
    return _split_heading(line)[0]


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in (text or "").splitlines():
        heading, inline = _split_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            if inline:
                sections[current].append(inline)
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _strip_labels(text: str) -> str:
    return re.sub(
        r"^(?:FACT|INFERENCE|RECOMMENDATION|UNKNOWN|EVIDENCE-BACKED FACT|DETERMINISTIC RESULT|EXPLICIT INFERENCE|UNSUPPORTED FACT|ASSUMPTION)\s*:\s*",
        "",
        text or "",
        flags=re.I | re.M,
    ).strip()


def _body_filled(body: str) -> bool:
    text = _strip_labels(body or "").strip()
    if len(text) < 40:
        return False
    lowered = text.lower()
    if lowered.startswith("unresolved"):
        return False
    if any(phrase in lowered for phrase in _BOILERPLATE_BODY) and len(text) < 220:
        return False
    return True


def _pricing_ok(body: str, evidence_blob: str) -> bool:
    if not _body_filled(body):
        return False
    lowered = body.lower()
    if "assumption" in lowered:
        return True
    amounts = re.findall(r"£\s*[\d,]+(?:\.\d+)?", body)
    corpus = (evidence_blob or "").replace(" ", "").replace(",", "")
    if amounts and all(amount.replace(" ", "").replace(",", "") in corpus for amount in amounts):
        return True
    if not amounts and ("opened" in lowered or "page" in lowered or "source" in lowered):
        return True
    return False


def _advert_ok(sections: dict[str, str]) -> bool:
    advert = sections.get("advert") or ""
    headline = sections.get("headline") or ""
    body = sections.get("body") or ""
    cta = sections.get("call to action") or ""
    combined = "\n".join(part for part in (advert, headline, body, cta) if part)
    if not _body_filled(combined):
        return False
    lowered = combined.lower()
    has_headline = "headline" in lowered or _body_filled(headline) or bool(re.search(r"^headline\s*:", combined, re.I | re.M))
    has_body = "body" in lowered or _body_filled(body) or len(advert) >= 80
    has_cta = "call to action" in lowered or _body_filled(cta) or "cta" in lowered
    return has_headline and has_body and has_cta


def plan_sections_filled(text: str, evidence_blob: str = "") -> bool:
    """True only when the owner sections are written, not left as Unresolved."""
    sections = parse_sections(text)
    if not sections:
        return False
    for key in _REQUIRED:
        if key == "advert":
            if not _advert_ok(sections):
                return False
            continue
        if key == "call to action":
            own = sections.get("call to action") or ""
            advert = sections.get("advert") or ""
            if _body_filled(own):
                continue
            if "call to action" in advert.lower() and _body_filled(advert):
                continue
            return False
        if key == "pricing":
            if not _pricing_ok(sections.get("pricing") or "", evidence_blob):
                return False
            continue
        if not _body_filled(sections.get(key) or ""):
            return False
    return True


def clip_at_boundary(text: str, limit: int) -> str:
    """Shorten stored text on a sentence or word boundary. Never end mid-word."""
    raw = re.sub(r"[ \t]+", " ", (text or "").strip())
    if len(raw) <= limit:
        return raw
    window = raw[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
    if cut >= 40:
        return window[: cut + 1].strip()
    space = window.rfind(" ")
    if space >= 40:
        return window[:space].strip()
    return window.strip()


def evidence_blob(evidence: list[dict] | None) -> str:
    parts = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        parts.append(" ".join([
            str(item.get("source_url") or item.get("url") or ""),
            str(item.get("source_title") or item.get("title") or ""),
            str(item.get("extracted_content") or item.get("text") or ""),
        ]))
    return "\n".join(parts)


def relevant_evidence(objective: str, evidence: list[dict] | None) -> list[dict]:
    kept = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or item.get("url") or "")
        if not url.startswith("http"):
            continue
        if page_is_relevant(
            objective,
            str(item.get("source_title") or item.get("title") or ""),
            str(item.get("extracted_content") or item.get("text") or ""),
            url,
        ):
            kept.append(item)
    return kept


def business_can_complete(findings: str, evidence: list[dict] | None, objective: str) -> bool:
    """COMPLETED needs relevant opened pages and filled sections. Empty safety text cannot pass."""
    blob = evidence_blob(evidence)
    if not plan_sections_filled(findings or "", blob):
        return False
    return bool(relevant_evidence(objective, evidence))


def publish_business_plan(objective: str, research: dict | None, model_text: str) -> str:
    """Turn model prose into the owner plan. An unfilled draft is returned empty."""
    research = research or {}
    evidence = relevant_evidence(objective, research.get("evidence") or [])
    blob = evidence_blob(evidence)
    sections = parse_sections(model_text or "")
    if not sections:
        return ""
    from .grounding import ground_text

    cleaned: dict[str, str] = {}
    for key, body in sections.items():
        grounded = ground_text(body, blob, objective or "")
        prose = _strip_labels(grounded.get("text") or "")
        if not prose:
            prose = _strip_labels(body)
        cleaned[key] = prose
    draft = _format_sections(cleaned, evidence)
    if not plan_sections_filled(draft, blob):
        return ""
    return draft


def _format_sections(sections: dict[str, str], evidence: list[dict]) -> str:
    lines = ["Business launch plan", ""]
    order = [key for key, _aliases in _HEADINGS if key in sections or key in _REQUIRED]
    seen = []
    for key in order:
        if key in seen:
            continue
        seen.append(key)
        title = key[:1].upper() + key[1:]
        if key == "call to action":
            title = "Call to action"
        lines.append(title)
        body = (sections.get(key) or "").strip()
        if body:
            lines.append(body)
        if key == "competitor and market research" and evidence:
            lines.append("Opened sources")
            for item in evidence[:8]:
                lines.append(item.get("source_title") or item.get("title") or "Opened page")
                lines.append(f"URL: {item.get('source_url') or item.get('url')}")
                excerpt = clip_at_boundary(item.get("extracted_content") or item.get("text") or "", 2000)
                if excerpt:
                    lines.append(excerpt)
                lines.append("")
        lines.append("")
    if "nothing was sent" not in "\n".join(lines).lower():
        lines.append("Nothing was sent. No purchase was made.")
    return "\n".join(lines).strip() + "\n"
