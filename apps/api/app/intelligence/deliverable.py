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
    "plan", "planned", "prepare", "pricing", "problem", "public",     "real",
    "research", "service", "site", "sites", "step", "steps", "that", "the",
    "businesses", "briefing", "launch", "answered", "willing", "travel",
    "invent", "guarantees", "testimonials",
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
_SEARCH_GLUE = {
    "price", "prices", "cost", "costs", "company", "companies",
    "near", "quote", "quotes", "shop", "shops", "supplier", "suppliers",
    "review", "reviews", "guide", "guides",
}
_ANCHORED = {
    "service",
    "problem",
    "offer and positioning",
    "competitor and market research",
    "pricing",
    "advert",
}
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


def _negated_terms(objective: str) -> set[str]:
    """Words in a sentence that says they are not the service."""
    negated = set()
    for sentence in re.split(r"[.!?]", objective or ""):
        lowered = sentence.lower()
        if not re.search(r"\b(not|other)\b", lowered):
            continue
        for word in re.findall(r"[a-z][a-z0-9'+-]{3,}", lowered):
            token = word.strip("'+-")
            if len(token) >= 4 and token not in _GENERIC_TERMS and token not in _QUERY_BOILERPLATE and token not in _PRONOUNS:
                negated.add(token)
    return negated


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
    words = lowered.split()
    if len(words) > 12:
        return True
    if len(words) > 14 and lowered.startswith(("the ", "this ", "a ")):
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
    if objective and not query_on_brief(query, objective):
        return "off_brief"
    return ""


def _term_matches(token: str, terms: list[str]) -> bool:
    for term in terms:
        if token == term or token in term or term in token:
            return True
        if len(token) >= 5 and len(term) >= 5 and token[:5] == term[:5]:
            return True
    return False


def query_on_brief(quote: str, objective: str) -> bool:
    """Every content word has to come from this objective. A foreign trade does not."""
    terms = distinctive_terms(objective)
    if not terms:
        return False
    tokens = re.findall(r"[a-z0-9']+", (quote or "").lower())
    content = [
        token for token in tokens
        if len(token) >= 4
        and token not in _QUERY_BOILERPLATE
        and token not in _SEARCH_GLUE
        and token not in _GENERIC_TERMS
        and token not in _PRONOUNS
    ]
    if not content:
        return False
    return all(_term_matches(token, terms) for token in content)


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
        if token in _negated_terms(objective):
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
    return queries


def _blob_has_term(term: str, blob: str) -> bool:
    if term in (blob or ""):
        return True
    if len(term) < 5:
        return False
    prefix = term[:5]
    return any(len(word) >= 5 and word.startswith(prefix) for word in re.findall(r"[a-z0-9]+", blob or ""))


def service_brief(objective: str) -> str:
    """The sentence that names the service. An instruction or a negated comparison is not the brief."""
    anchors = [term for term in distinctive_terms(objective) if len(term) >= 5][:6]
    best = ""
    best_score = 0
    for sentence in re.split(r"[.!?]", objective or ""):
        lowered = sentence.lower()
        if re.search(r"\b(not|other)\b", lowered):
            continue
        score = sum(1 for term in anchors if term in lowered)
        if score > best_score and len(sentence.strip()) >= 20:
            best = re.sub(r"\s+", " ", sentence).strip()
            best_score = score
    if best:
        return best[:280]
    return " ".join(anchors)[:280]


def anchor_terms(objective: str) -> list[str]:
    """The earliest service words. A later comparison is not the search."""
    return [term for term in distinctive_terms(objective) if len(term) >= 5][:4]


def section_has_anchor(kind: str, body: str, objective: str) -> bool:
    if kind not in _ANCHORED:
        return True
    anchors = anchor_terms(objective)
    if not anchors:
        return True
    return any(_blob_has_term(term, (body or "").lower()) for term in anchors)


def page_is_relevant(objective: str, title: str = "", text: str = "", url: str = "") -> bool:
    """True when the page shares this objective's service words. A fetch is not evidence."""
    terms = [term for term in distinctive_terms(objective) if len(term) >= 5]
    if not terms:
        return True
    blob = f"{title}\n{text}\n{url}".lower()
    hits = [term for term in terms if _blob_has_term(term, blob)]
    if len(hits) >= 2:
        return True
    if len(hits) == 1 and len(hits[0]) >= 8:
        return True
    return False


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


_SCRATCH = (
    "let me ", "i need to", "i'll ", "i’ll ", "i will ", "i have evidence", "i think",
    "what i need", "this needs to be",
    "okay, the user", "the user wants", "the user needs", "the user specified", "the user provided",
    "looking at the context", "looking at the details", "i should", "i must", "hmm,", "hmm ",
    "they need the", "they want me", "important constraint", "the key is",
    "the user has", "the user is", "the user specifically", "/no_think", "two or three sentences",
    "i see what", "i've identified", "i have identified", "you've done", "you have done",
    "we are given", "we are to", "the instruction", "the problem says", "let's write", "lets write",
    "would you like", "for example:", "for example,", "200+", "reached out", "potential homeowner",
    "the phrase", "the answer is", "i'm not sure", "i am not sure", "without additional context",
    "it sounds like", "previous response", "specific format", "line starting", "catchy phrase",
    "i'm here to help", "i am here to help", "0 doors", "logic puzzle", "cabinet meeting",
    "looking at the sources", "the most relevant", "the user says", "wait, the", "wait,",
    "home search", "research phase", "common situation", "or check if", "specific way",
    "you're using", "you are using", "is a phrase", "this seems",
    "if nothing was sent", "no doors", "not a standard", "standard phrase",
    "nothink", "no_think", "/no_think",
)
_REFUSAL = (
    "doesn't state", "does not state", "doesn't specify", "does not specify",
    "doesn't explicitly", "does not explicitly",
)


def _heading_name(line: str) -> bool:
    lowered = line.strip().strip("#*_ ").lower().rstrip(":")
    if not lowered:
        return False
    for key, aliases in _HEADINGS:
        if lowered == key or lowered in aliases:
            return True
    return False


def narration_reason(line: str) -> str:
    """Why this line cannot be stored. Empty when the line passes the check."""
    stripped = line.strip()
    if not stripped or _heading_name(stripped):
        return ""
    lowered = stripped.lower()
    if re.fullmatch(r"\d+\.?", lowered):
        return "The line is a bare number, not a sentence."
    if lowered in {"unresolved.", "this is unresolved.", "this is unresolved", "a full sentence.", "a full sentence"}:
        return "The line only says unresolved."
    if lowered.startswith(("unresolved -", "unresolved:")):
        return "The line only says unresolved."
    for phrase in _SCRATCH:
        if phrase in lowered:
            return f"The line is drafting narration ({phrase.strip()})."
    compact = re.sub(r"\s+", "", lowered)
    if re.search(r"(.{1,4})\1{6,}", compact):
        return "The line is repeated characters, not a sentence."
    if "24/7" in lowered:
        return "The line claims 24/7 availability. That claim is not stored."
    if "always ready" in lowered:
        return "The line claims the service is always ready. That claim is not stored."
    if "monitoring system" in lowered:
        return "The line describes a monitoring system, which this request does not ask for."
    if "mortgage" in lowered or "loan-to-value" in lowered:
        return "The line is about a mortgage, which this request does not ask for."
    if re.search(
        r"\b(?:1 sentence|one sentence|exactly three lines|output only the|previous answer was|finished lines|answered\s*:)\b",
        lowered,
    ):
        return "The line repeats the instruction instead of answering it."
    refusal = any(phrase in lowered for phrase in _REFUSAL)
    if refusal and not any(phrase in lowered for phrase in ("assumption", "recommend", "the offer is", "should ")):
        return "The line refuses to write the section."
    return ""


def _narration_line(line: str) -> bool:
    """One worksheet line. A heading is kept so the sections still parse."""
    return bool(narration_reason(line))


def finished_lines(text: str) -> str:
    """Drop drafting narration and repeated lines. Keep the sentences the owner can use."""
    kept: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if _narration_line(stripped):
            continue
        key = re.sub(r"\s+", " ", stripped.lower())
        if key in seen:
            continue
        seen.add(key)
        kept.append(stripped)
    return "\n".join(kept).strip()


def _owner_prose(body: str) -> str:
    """Section sentences, without the opened-page list that is appended for the record."""
    text = body or ""
    match = re.search(r"\nopened sources\b", text, re.I)
    if match:
        text = text[: match.start()]
    return text.strip()


def detach_glued(line: str) -> str:
    """A stem that was glued onto a new sentence is not the sentence."""
    text = re.sub(r"\b(.{12,}?)\s+\1", r"\1", (line or "").strip(), count=1, flags=re.I)
    if re.match(r"^(headline|body|call to action|assumption)\s*:", text, re.I):
        return text
    match = re.match(
        r"^(?P<left>.{0,80}?\b(?:wants|is|through|replacement|assumes))\s+(?P<right>[A-Z].{30,}[.!?])$",
        text,
    )
    if match and len(match.group("left").split()) <= 10:
        return match.group("right").strip()
    return text


_OWNER_STEMS = (
    "the service is ",
    "the customer is ",
    "the problem is ",
    "the offer is ",
    "opened pages state ",
    "assumption:",
    "customers are reached through ",
    "headline:",
    "ask for ",
    "next, the owner ",
    "the plan assumes ",
    "still open:",
)


def _draft_clause(sentence: str) -> str:
    """A drafting lead in front of a finished sentence is not the sentence."""
    match = re.match(
        r"^(?:alternatively|example|for example|we can say|let me try|we can also|note)[^:]{0,48}:\s*[\"']?(.+?)[\"']?\s*$",
        (sentence or "").strip(),
        re.I,
    )
    if not match:
        return (sentence or "").strip()
    clause = match.group(1).strip().strip("\"'")
    return clause or (sentence or "").strip()


def _visible_answer(text: str) -> str:
    """The lines after the last think close. An unclosed think is the whole reply."""
    content = text or ""
    at = content.lower().rfind("</think>")
    if at != -1:
        content = content[at + len("</think>") :]
    return re.sub(r"/no_think", "", content, flags=re.I).strip()


def _without_narration(sentence: str) -> str:
    """A note and the owner sentence on one line still leave the owner sentence."""
    text = (sentence or "").strip()
    if not text or not _narration_line(text):
        return text
    lowered = text.lower()
    for stem in _OWNER_STEMS:
        at = lowered.find(stem)
        if at < 0:
            continue
        clause = text[at:].strip()
        if clause and not _narration_line(clause):
            return clause
    return ""


def _usable_sentences(text: str) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        piece = detach_glued(line)
        if not piece:
            continue
        parts = re.split(r"(?<=[.!?])\s+", piece) if re.search(r"[.!?]", piece) else [piece]
        for sentence in parts:
            sentence = re.sub(r"[*_]{1,3}", "", sentence).strip(" \t-\"'")
            sentence = _draft_clause(sentence)
            sentence = _without_narration(sentence)
            if not sentence or _narration_line(sentence):
                continue
            if re.fullmatch(r"(?:still open|assumption)\s*:\s*\d+\.?", sentence, re.I):
                continue
            key = re.sub(r"\s+", " ", sentence.lower())
            if key in seen:
                continue
            seen.add(key)
            kept.append(sentence)
    return kept


_ADVERT_BAD = (
    "format", "starting with", "catchy", "[text]", "[some",
    "placeholder", "a line about", "one sentence", "write exactly", "then stop",
)


def _advert_content_ok(label: str, content: str) -> bool:
    content = (content or "").strip().strip('"').strip()
    if len(content) < 12 or len(content) > 180:
        return False
    if _narration_line(content):
        return False
    lowered = content.lower()
    if any(phrase in lowered for phrase in _ADVERT_BAD):
        return False
    if lowered.rstrip(" .:") in {"headline", "body", "call to action"}:
        return False
    return True


def _advert_from_lines(lines: list[str]) -> str:
    """The first real Headline, Body, and Call to action. Notes around them are not the advert."""
    headline = body = cta = ""
    plain: list[str] = []
    for line in lines:
        stripped = line.strip().strip('"')
        unlabeled = re.sub(r"^(headline|body|call to action)\s*:\s*", "", stripped, flags=re.I).strip()
        if unlabeled and _advert_content_ok("body", unlabeled) and unlabeled not in plain:
            plain.append(unlabeled)
        match = re.match(r"^(headline|body|call to action)\s*:\s*(.+)$", stripped, re.I)
        if match:
            label = match.group(1).lower()
            content = match.group(2).strip().strip('"')
            if not _advert_content_ok(label, content):
                continue
            if label == "headline" and not body:
                headline, body, cta = content, "", ""
            elif label == "body" and headline and not body:
                body = content
            elif label == "call to action" and headline and body and not cta:
                cta = content
            if headline and body and cta:
                break
            continue
        if headline and not body and _advert_content_ok("body", stripped):
            body = stripped
            continue
        if headline and body and not cta and _advert_content_ok("call to action", stripped):
            cta = stripped
            break
    if not (headline and body and cta) and len(plain) >= 3:
        headline, body, cta = plain[0], plain[1], plain[2]
    if not (headline and body and cta):
        return ""
    return f"Headline: {headline}\nBody: {body}\nCall to action: {cta}"


def _prefer_request(kind: str, sentences: list[str]) -> list[str]:
    """An instruction echo is already gone. A reply that asks the customer comes first."""
    if kind != "call to action":
        return sentences
    asked = [
        sentence for sentence in sentences
        if re.search(r"\b(ask|please|reply|could you|would you|let us know|confirm)\b", sentence, re.I)
    ]
    if not asked:
        return sentences
    return sorted(asked, key=len, reverse=True)


def owner_section(kind: str, text: str) -> str:
    """The lines an owner can use. A prompt essay is not returned."""
    prose = _owner_prose(_visible_answer(text or ""))
    if kind == "advert":
        return _advert_from_lines(_usable_sentences(prose))
    sentences = _usable_sentences(prose)
    if kind == "pricing":
        picked = []
        for sentence in sentences:
            if "$" in sentence or re.fullmatch(r"assumption:\s*\d+\.?", sentence, re.I):
                continue
            if "£" in sentence or "assumption" in sentence.lower():
                picked.append(sentence)
        if not picked:
            return ""
        body = "\n".join(picked[:6])
        if "assumption" not in body.lower():
            body = "Assumption: " + body
        return body
    if kind == "competitor and market research":
        picked = [sentence for sentence in sentences if "£" in sentence or "http" in sentence.lower()]
        return "\n".join((picked or sentences)[:6])
    limit = 2 if kind == "assumptions" else 1
    body = "\n".join(_prefer_request(kind, sentences)[:limit]).strip()
    if body and not re.search(r"[.!?]$", body):
        body += "."
    return body


def _narrated(text: str) -> bool:
    """A drafting worksheet is not a section the owner can use."""
    lowered = (text or "").lower()
    if any(phrase in lowered for phrase in _SCRATCH):
        return True
    if any(phrase in lowered for phrase in ("mortgage", "loan-to-value", "24/7", "always ready", "monitoring system")):
        return True
    compact = re.sub(r"\s+", "", lowered)
    if re.search(r"(.{1,4})\1{6,}", compact):
        return True
    if lowered.count("unresolved") >= 2:
        return True
    if any(phrase in lowered for phrase in _REFUSAL):
        return True
    return False


def _body_filled(body: str) -> bool:
    text = _strip_labels(_owner_prose(body or "")).strip()
    if len(text) < 40 or len(text) > 900:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 8:
        return False
    if not re.search(r"[.!?]", text):
        return False
    lowered = text.lower()
    if lowered.startswith("unresolved"):
        return False
    if _narrated(text):
        return False
    if any(_narration_line(line) for line in lines):
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
    """The stored advert is three finished lines, not an essay that mentions the word headline."""
    advert = _owner_prose(sections.get("advert") or "")
    extra = ""
    if "call to action" not in advert.lower():
        extra = _owner_prose(sections.get("call to action") or "")
    prose = "\n".join(
        part for part in (advert, sections.get("headline") or "", sections.get("body") or "", extra) if part
    )
    lines = [line.strip() for line in prose.splitlines() if line.strip()]
    if not lines or len(lines) > 5 or len(prose) > 900:
        return False
    if any(_narration_line(line) for line in lines):
        return False
    return bool(owner_section("advert", prose))


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


def _body_rejection(body: str) -> str:
    text = _strip_labels(_owner_prose(body or "")).strip()
    if len(text) < 40:
        return f"The section is too short to use ({len(text)} characters)."
    if len(text) > 900:
        return "The section is too long to be a finished answer."
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 8:
        return "The section is a worksheet, not a finished answer."
    if not re.search(r"[.!?]", text):
        return "The section is not a finished sentence."
    if text.lower().startswith("unresolved"):
        return "The section still says unresolved."
    for line in lines:
        reason = narration_reason(line)
        if reason:
            return reason
    lowered = text.lower()
    if any(phrase in lowered for phrase in _BOILERPLATE_BODY) and len(text) < 220:
        return "The section is the blank form."
    if _narrated(text):
        return "The section is drafting narration."
    return ""


def section_rejection(key: str, raw: str, blob: str = "", objective: str = "") -> str:
    """A readable reason this check failed. Empty when the section passes."""
    if not (raw or "").strip():
        return "The model returned nothing."
    cleaned = owner_section(key, raw)
    if not cleaned:
        for line in _visible_answer(raw).splitlines():
            reason = narration_reason(line)
            if reason:
                return reason
        if key == "pricing":
            return "Pricing needs an assumption or a £ amount from an opened page."
        if key == "advert":
            return "The advert needs a headline, one body sentence, and a call to action."
        return "The sentence filter removed every line."
    if key == "pricing":
        if not _pricing_ok(cleaned, blob):
            return "Pricing has no assumption, and the £ amount is not on an opened page."
    elif key == "advert":
        if not _advert_ok({"advert": cleaned}):
            for line in cleaned.splitlines():
                reason = narration_reason(line)
                if reason:
                    return reason
            return "The advert is not three finished lines."
    elif not _body_filled(cleaned):
        return _body_rejection(cleaned) or "The section did not pass the sentence check."
    if not section_has_anchor(key, cleaned, objective):
        return "The section does not use the service words from this request."
    return ""


def quote_draft(text: str) -> str:
    """The model's words, marked so a later check cannot mistake them for a passed plan."""
    lines = ["> " + line if line.strip() else ">" for line in (text or "").splitlines()]
    return "\n".join(lines).strip()


def retained_draft(model_text: str, objective: str, blob: str = "", reasons: dict | None = None) -> str:
    """The full draft and one reason per failed check. This is not a passed plan."""
    from_text = parse_sections(model_text or "")
    lines = [
        "Validation rejected this draft. No check was relaxed.",
        "",
    ]
    failed = 0
    for key in _REQUIRED:
        reason = (reasons or {}).get(key) or section_rejection(key, from_text.get(key) or "", blob, objective)
        if reason:
            failed += 1
            lines.append(f"[{key}] {reason}")
        else:
            lines.append(f"[{key}] passed")
    if not failed and (model_text or "").strip():
        lines.append("[plan] The draft did not pass every check together.")
    lines += ["", "Full draft:", quote_draft(model_text or ""), "", "Nothing was sent."]
    return "\n".join(lines).strip() + "\n"


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
    sections = parse_sections(finished_lines(model_text or ""))
    if not sections:
        return ""
    extracted: dict[str, str] = {}
    for key, body in sections.items():
        if key == "advert" and "call to action" not in (body or "").lower():
            body = f"{body}\n{sections.get('call to action') or ''}"
        kept = owner_section(key, body)
        if kept and not section_has_anchor(key, kept, objective):
            kept = ""
        if kept:
            extracted[key] = kept
    if not extracted:
        return ""
    sections = extracted
    from .grounding import ground_text

    cleaned: dict[str, str] = {}
    for key, body in sections.items():
        grounded = ground_text(body, blob, objective or "")
        prose = _strip_labels(grounded.get("text") or "")
        if not prose:
            prose = _strip_labels(body)
        if key == "pricing" and prose and "assumption" not in prose.lower():
            prose = "Assumption: " + prose
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
