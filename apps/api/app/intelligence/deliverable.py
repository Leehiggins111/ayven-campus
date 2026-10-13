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
    # A substring of an instruction word ("avoid" inside "avoids") is not a service word.
    # The query has to contain one of the objective's own words, including a later name.
    terms = [term for term in distinctive_terms(objective) if len(term) >= 4] if objective else []
    if terms and not any(token == term for token in content for term in terms):
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
        short = re.sub(r"[\"“”]", "", (item or "")).strip()
        short = re.sub(r"\s+", " ", short)[:180]
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
    """Searches about the service, the place, prices, and named businesses. Not the requester."""
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
    company = " ".join(terms[:4] + ([region] if region else []) + ["company"])
    company = re.sub(r"\s+", " ", company).strip()
    if company and company not in queries and not reject_search_query(company, objective):
        queries.append(company[:180])
    return queries


_REPORT_HOSTS = (
    "linkedin.com",
    "intelmarketresearch",
    "qyresearch",
    "futuredatastats",
    "aviaan",
    "6wresearch",
    "grandviewresearch",
    "marketresearchfuture",
    "statista.com",
    "ibisworld",
    "cognitivemarketresearch",
    "alliedmarketresearch",
    "databridgemarketresearch",
    "fortunebusinessinsights",
    "researchandmarkets",
)
_GUIDE_TITLE = re.compile(
    r"\b(how to|how much|cost to|cost guide|price guide|price breakdown|full price guide|"
    r"market size|market report|industry report|cagr)\b",
    re.I,
)


def market_report_page(url: str = "", title: str = "", text: str = "") -> bool:
    """A market-size or LinkedIn SEO page is not evidence for a business fact."""
    blob = f"{url} {title}".lower()
    if "linkedin.com" in blob or "linkedin.com" in (text or "").lower()[:240]:
        return True
    if any(token in blob for token in _REPORT_HOSTS):
        return True
    if re.search(r"\b(market size|market report|industry report|cagr)\b", blob, re.I):
        return True
    return False


def listing_page(url: str = "") -> bool:
    """A task marketplace is not a business that sells the service."""
    return "airtasker.com" in (url or "").lower()


def guide_page(url: str = "", title: str = "") -> bool:
    """A how-to or a cost guide is not a competitor's own offer."""
    if market_report_page(url, title):
        return True
    if re.search(r"/(guides?|blog|news|costs|cost-guides|posts?|directory)/", url or "", re.I):
        return True
    if re.search(r"how-much|cost-guide|price-guide", url or "", re.I):
        return True
    return bool(_GUIDE_TITLE.search(title or ""))


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
    hits = _relevance_hits(objective, title, text, url)
    if len(hits) >= 2:
        return True
    if len(hits) == 1 and len(hits[0]) >= 8:
        return True
    return False


def relevance_reason(objective: str, title: str = "", text: str = "", url: str = "") -> str:
    """Why this opened page counts. Empty when it does not."""
    if not page_is_relevant(objective, title, text, url):
        return ""
    hits = _relevance_hits(objective, title, text, url)
    if not hits:
        return "The page was opened for this request."
    return "Relevant because the page uses: " + ", ".join(hits[:5]) + "."


def _relevance_hits(objective: str, title: str = "", text: str = "", url: str = "") -> list[str]:
    terms = [term for term in distinctive_terms(objective) if len(term) >= 5]
    blob = f"{title}\n{text}\n{url}".lower()
    return [term for term in terms if _blob_has_term(term, blob)]


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


def _continues_sentence(previous: str, line: str) -> bool:
    """A markdown hard break, or a line split before the sentence ends, is still one sentence."""
    if not previous or not line.strip():
        return False
    if _split_heading(line)[0]:
        return False
    if re.match(r"^(?:headline|body|call to action|assumption)\s*:", line.strip(), re.I):
        return False
    if previous.endswith("  "):
        return True
    stripped = previous.rstrip()
    return bool(stripped) and not re.search(r"[.!?]$", stripped) and line[:1].islower()


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    pending = ""
    for line in (text or "").splitlines():
        heading, inline = _split_heading(line)
        if heading:
            if current and pending:
                sections[current].append(pending)
            pending = ""
            current = heading
            sections.setdefault(current, [])
            if inline:
                pending = inline
            continue
        if not current:
            continue
        if _continues_sentence(pending, line):
            pending = pending.rstrip() + " " + line.strip()
            continue
        if pending:
            sections[current].append(pending)
        pending = line.strip()
    if current and pending:
        sections[current].append(pending)
    return {key: "\n".join(part for part in lines if part.strip()).strip() for key, lines in sections.items()}


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


def narration_reason(line: str, objective: str = "", blob: str = "") -> str:
    """Why this line cannot be stored. Empty when the line passes the check."""
    stripped = line.strip()
    if not stripped or _heading_name(stripped):
        return ""
    lowered = stripped.lower()
    if re.fullmatch(r"\d+\.?", lowered):
        return "The line is a bare number, not a sentence."
    if re.fullmatch(r"\[f\d+\]\.?", lowered):
        return "The line is only a citation."
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
    if re.search(r"\b(let's|let’s|i think|we need to|the user|they need me|service words|kind of buyer|in this context|might be tricky|without avoiding|full damage|not a step|can you help me)\b", lowered):
        return "The line is model self-talk, not a sentence the owner can use."
    if re.search(r"\bask\s+\d+\b", lowered):
        return "The line is model self-talk, not a sentence the owner can use."
    if re.search(r"\b(name a customer|name a problem|name the owner|name two opened|crafting the|looking at the prices|does not ask the customer|does not name a|ask the customer to take a step)\b", lowered):
        return "The section describes the task instead of answering it."
    if re.search(r"\b(does not imply|do not imply|service name that|hours or availability)\b", lowered):
        return "The line repeats the instruction instead of answering it."
    if "24/7" in lowered and "24/7" not in (objective or "").lower() and not (
        "[f" in lowered and "24/7" in (blob or "").lower()
    ):
        return "The line claims 24/7 availability. That claim is not stored."
    if "always ready" in lowered:
        return "The line claims the service is always ready. That claim is not stored."
    if "monitoring system" in lowered:
        return "The line describes a monitoring system, which this request does not ask for."
    if "mortgage" in lowered or "loan-to-value" in lowered:
        return "The line is about a mortgage, which this request does not ask for."
    if re.search(
        r"\b(?:1 sentence|one sentence|exactly three lines|output only the|previous answer was|previous line was|finished lines|answered\s*:|availability claim|not stored|do not mention hours|finish the sentence|finishes the sentence|might finish|might say)\b",
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
    # "£1,300, though the page does not specify" still has the price. Drop the refusal clause.
    for phrase in _REFUSAL:
        at = lowered.find(phrase)
        if at <= 0:
            continue
        head = re.sub(
            r"(?:,\s*|\s+)(?:though|although|but|and|while|because|since)\s+(?:the\s+page\s+)?$",
            "",
            text[:at],
            flags=re.I,
        ).strip(" ,;:-")
        if "£" not in head or _narration_line(head) or len(head.split()) < 4:
            continue
        if not re.search(r"[.!?]$", head):
            head += "."
        return head
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
    "should be", "must be", "should mention", "from the pages", "three lines",
    "benefit-focused", "actionable", "the example", "for this context", "need three",
)


def _advert_content_ok(label: str, content: str) -> bool:
    content = (content or "").strip().strip('"').strip()
    if len(content) < 12 or len(content) > 180:
        return False
    if _narration_line(content):
        return False
    lowered = content.lower()
    for phrase in _ADVERT_BAD:
        if " " in phrase or phrase.startswith("["):
            hit = phrase in lowered
        else:
            hit = bool(re.search(rf"\b{re.escape(phrase)}\b", lowered))
        if hit:
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
            elif label == "call to action" and headline and body:
                cta = content
            if headline and body and cta:
                break
            continue
        if headline and not body and _advert_content_ok("body", stripped):
            body = stripped
            continue
        if headline and body and not cta and _advert_content_ok("call to action", stripped):
            cta = stripped
            continue
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
            if "£" in sentence or "assumption" in sentence.lower() or re.search(r"\bmeasur", sentence, re.I):
                picked.append(sentence)
        if not picked:
            return ""
        return "\n".join(picked[:8])
    if kind == "competitor and market research":
        picked = [
            sentence for sentence in sentences
            if "£" in sentence or "http" in sentence.lower() or re.search(
                r"\b(differs?|rather|instead|unlike|offers?|hires?|rents?|rental|sells?|supply|supplies|installs?|charges?|replaces?|replacement|services?)\b",
                sentence,
                re.I,
            )
        ]
        return "\n".join((picked or sentences)[:6])
    # The required fact is often in the second sentence. One sentence is not the section.
    body = "\n".join(_prefer_request(kind, sentences)[:3]).strip()
    if body and not re.search(r"[.!?]$", body):
        body += "."
    return body


def _narrated(text: str) -> bool:
    """A drafting worksheet is not a section the owner can use."""
    lowered = (text or "").lower()
    if any(phrase in lowered for phrase in _SCRATCH):
        return True
    if any(phrase in lowered for phrase in ("mortgage", "loan-to-value", "always ready", "monitoring system")):
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


def sections_for_gate(text: str) -> dict[str, str]:
    """The sections the product gate checks. Advert includes the call to action when it was parsed apart."""
    sections = dict(parse_sections(text or ""))
    advert = sections.get("advert") or ""
    extra = sections.get("call to action") or ""
    if extra and "call to action" not in advert.lower():
        sections["advert"] = (advert + "\nCall to action: " + extra).strip()
    if not (sections.get("call to action") or "").strip() and advert:
        sections["call to action"] = advert
    return sections


def plan_sections_filled(text: str, evidence_blob: str = "", objective: str = "") -> bool:
    """True only when the owner sections are written, not left as Unresolved."""
    sections = sections_for_gate(text)
    if not sections:
        return False
    for key in _REQUIRED:
        raw = sections.get(key) or ""
        if any(narration_reason(line) for line in raw.splitlines()):
            return False
        if section_rejection(key, raw, evidence_blob, objective):
            return False
    return True


def _body_rejection(body: str) -> str:
    text = _strip_labels(_owner_prose(body or "")).strip()
    if len(text) < 40:
        return "The section is too short to use."
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


_IDENTITY = re.compile(
    r"\b[A-Z][a-z]+(?:['’]s)?(?:[ \t]+[A-Z][a-z]+(?:['’]s)?){1,5}\b"
)


_NOT_A_BORROWED_NAME = {
    "your", "our", "this", "that", "with", "from", "into",
    "business", "businesses", "company", "service", "services",
    "coffee", "machine", "machines", "commercial", "premium",
    "rental", "solution", "solutions", "weekly", "flexible",
}


def borrowed_service_name(body: str, objective: str, blob: str) -> str:
    """A business name taken from an opened page is not this request's service."""
    words = set(re.findall(r"[a-z0-9]+", (objective or "").lower()))
    body_l = (body or "").lower()
    found = []
    for match in _IDENTITY.finditer(blob or ""):
        phrase = re.sub(r"\s+", " ", match.group(0)).strip()
        candidates = [phrase]
        if phrase.lower().startswith("at "):
            candidates.append(phrase[3:].strip())
        matched = ""
        for candidate in candidates:
            parts = [
                part for part in re.findall(r"[a-z0-9]+", candidate.lower())
                if part not in {
                "s", "the", "and", "for", "uk", "at", "your", "our", "new",
                "across", "over", "than", "under", "after", "before", "within",
                "during", "through", "into", "their", "about", "from", "with",
                "more", "most",
            } and len(part) > 1
            ]
            stems = {word[:-1] if word.endswith("s") and len(word) > 4 else word for word in words}
            if len(parts) < 2 or all(
                part in words or (part[:-1] if part.endswith("s") and len(part) > 4 else part) in stems
                for part in parts
            ):
                continue
            if all(part in _NOT_A_BORROWED_NAME for part in parts):
                continue
            if candidate.lower() not in body_l:
                continue
            matched = candidate
            break
        if matched:
            found.append(matched)
    if not found:
        return ""
    phrase = max(found, key=len)
    return f"The name '{phrase}' comes from an opened page, not from the request."


_UNSUPPORTED_PROMISES = (
    "free consultation", "free quote", "free home visit", "free home",
    "in one day", "same day", "same-day", "guaranteed", "upfront cost",
)


def unsupported_promise(body: str, objective: str, blob: str) -> str:
    """A competitor's offer is not a promise of the business being created."""
    objective_l = (objective or "").lower()
    blob_l = (blob or "").lower()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        lowered = sentence.lower()
        if blob and _names_opened_page(sentence, blob):
            continue
        for phrase in _UNSUPPORTED_PROMISES:
            if phrase not in lowered or phrase in objective_l:
                continue
            if blob_l and phrase in blob_l and re.search(r"\[F\d+\]", sentence):
                continue
            return f"The section promises '{phrase}'. The request does not."
    return ""


def _pricing_omits_page_figure(body: str, blob: str) -> str:
    """A pricing section that never uses an opened £ figure is not a market reference."""
    amounts = _pound_amounts(blob)
    if not amounts:
        return ""
    if any(_number_on_page(re.sub(r"[£\s,]", "", amount), body or "") for amount in amounts):
        return ""
    return f"An opened page states '{amounts[0]}'. The pricing section does not use it."


def page_line_for_check(reason: str, blob: str) -> str:
    """The opened sentence the failed check named. The model still writes the section."""
    match = re.search(r"states '([^']+)'", reason or "", re.I)
    if not match:
        return ""
    phrase = match.group(1).lower()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", blob or ""):
        if phrase in sentence.lower() and "£" in sentence:
            return "The opened page says: " + re.sub(r"\s+", " ", sentence).strip()[:360]
    return ""


def names_requester(body: str, objective: str) -> str:
    for name in requester_names(objective):
        if name and re.search(rf"\b{re.escape(name)}\b", body or "", re.I):
            return "The section names the person who wrote the request."
    return ""


def section_rejection(key: str, raw: str, blob: str = "", objective: str = "") -> str:
    """A readable reason this check failed. Empty when the section passes."""
    if not (raw or "").strip():
        return "The model returned nothing."
    raw = _without_service_area(raw, objective)
    if not raw.strip():
        return "The section invents a service area. The request says not to."
    pasted = _pasted_page(raw)
    if pasted:
        return pasted
    tariff = _rate_card(raw)
    if tariff:
        return tariff
    if re.search(r"\d+\s*w\s*x\s*\d+", raw or "", re.I):
        return "The section pastes a machine specification. State the offer in a sentence."
    leaked = _example_leak(raw, objective, blob)
    if leaked:
        return leaked
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
        if _ungrounded_amount(cleaned, blob):
            return "Pricing cites a £ figure that is not on an opened page and is not labelled as an assumption."
        if not _pricing_ok(cleaned, blob):
            return "Pricing has no assumption, and the £ amount is not on an opened page."
    elif _ungrounded_amount(cleaned, blob):
        return "The section cites a £ figure that is not on an opened page and is not labelled as an assumption."
    elif key == "advert":
        if not _advert_ok({"advert": cleaned}):
            for line in cleaned.splitlines():
                reason = narration_reason(line)
                if reason:
                    return reason
            return "The advert is not three finished lines."
    elif key == "call to action":
        if not _body_filled(cleaned) and not _advert_ok({"advert": raw}):
            return _body_rejection(cleaned) or "The call to action is too short to use."
    elif not _body_filled(cleaned):
        return _body_rejection(cleaned) or "The section did not pass the sentence check."
    if _unsupported_percent(cleaned, blob):
        return "The section states a percentage that is not on an opened page."
    if _invented_saving(cleaned, blob):
        return "The section states a saving that is not on an opened page."
    if _invented_service_area(cleaned, objective):
        return "The section invents a service area. The request says not to."
    if _confirm_request(cleaned):
        return "The section asks the reader to confirm the request."
    if _echoes_objective(cleaned, objective):
        return "The section repeats the objective instead of answering it."
    if key in {"channels", "next steps", "advert", "offer and positioning", "service", "problem", "target customer"}:
        named_owner = names_requester(cleaned, objective)
        if named_owner:
            return named_owner
    if key == "problem":
        promised = unsupported_promise(cleaned, objective, blob)
        if promised:
            return promised
    if key in {"service", "offer and positioning", "advert", "call to action", "channels", "next steps"}:
        borrowed = borrowed_service_name(cleaned, objective, blob)
        if borrowed:
            return borrowed
        promised = unsupported_promise(cleaned, objective, blob)
        if promised:
            return promised
    mismatch = _claim_mismatches(cleaned, blob)
    if mismatch:
        return mismatch
    pasted = _pasted_page(cleaned)
    if pasted:
        return pasted
    cited_assumption = _assumption_on_fact(cleaned)
    if cited_assumption:
        return cited_assumption
    shape = _section_shape(key, cleaned, objective, blob)
    if shape:
        return shape
    if key != "pricing" and _pound_amounts(cleaned):
        unlinked = _unlinked_price(cleaned, blob, assumptions=False)
        if unlinked:
            return unlinked
    if not section_has_anchor(key, cleaned, objective):
        return "The section does not use the service words from this request."
    return ""


_BUYERS = (
    "homeowner", "household", "landlord", "tenant", "family", "families",
    "office", "renter", "buyer", "cafe", "restaurant", "hotel", "school",
    "couple", "occupier", "developer", "shop", "clinic", "pub", "venue",
    "company", "firm", "studio", "resident", "people", "person", "client",
    "business", "businesses", "owner",
)
_PROBLEM_BURDEN = (
    "cost", "expensive", "longer", "disruption", "waste", "budget", "delay",
    "price", "time", "cheaper", "afford", "slow", "inconvenient", "difficult",
    "buying", "outright", "challenge", "anxious", "hassle", "barrier", "struggle",
    "inconvenience",
)
_EXAMPLE_WORDS = ("bicycle", "bicycles", "rider", "riders", "cyclist", "cyclists", "chains", "alex")
_EXAMPLE_PHRASES = (
    "the example shows", "example of the shape", "not this request",
    "need three lines", "should be like", "for this context",
)
_ASK = re.compile(
    r"\b(ask|reply|call|phone|email|write|contact|book|request|quote|schedule|touch)\b",
    re.I,
)
_CONTRAST = (
    "differ", "rather", "instead", "without", "against", "compared", "unlike",
    "alternative", "avoids", "avoid", "shorter", "keeps", "stays", "in place",
    "leaves", "leaving",
)
_CHANNELS = (
    "search", "leaflet", "facebook", "instagram", "google", "referral",
    "directory", "email", "flyer", "poster", "marketplace", "nextdoor", "showroom",
    "press", "website", "seo", "social", "checkatrade", "gumtree",
    "neighbour", "neighbor", "whatsapp", "phone", "radio", "linkedin", "youtube",
    "mailshot", "mail", "workshop",
)
_OWNER_ACTIONS = (
    "confirm", "contact", "open", "write", "visit", "compare", "measure",
    "count", "draft", "publish", "phone", "email", "list", "choose", "check",
    "collect", "decide", "photograph", "call", "meet", "read", "ask", "record",
    "book", "research", "scope", "note", "post", "reach", "start", "use", "operate",
    "send", "share", "target", "outreach", "register", "schedule", "gather",
    "refine", "assess", "observe", "promote", "arrange", "install", "select",
    "source", "secure", "partner",
)
_CONFIRM_REQUEST = re.compile(
    r"\b(?:could you confirm|can you confirm|please confirm if|confirm if this|"
    r"confirm that this|is this what you|you(?:'re| are) looking for|"
    r"the solution you|as per the request|as per the instruction|"
    r"lee answered|the request is)\b",
    re.I,
)


def _has_word(text: str, word: str) -> bool:
    """directory matches directories. referral matches referrals."""
    if re.search(rf"\b{re.escape(word)}s?\b", text or "", re.I):
        return True
    if word.endswith("y"):
        return bool(re.search(rf"\b{re.escape(word[:-1])}ies\b", text or "", re.I))
    return False


def _has_action(text: str, word: str) -> bool:
    return bool(re.search(rf"\b{re.escape(word)}(?:s|ed|ing)?\b", text or "", re.I))


def _confirm_request(text: str) -> bool:
    return bool(_CONFIRM_REQUEST.search(text or ""))


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _echoes_objective(section: str, objective: str) -> bool:
    """A long copied stretch of the objective is not a section."""
    if not (section or "").strip() or not (objective or "").strip():
        return False
    if re.search(r"\b(?:lee answered|the request is|as per the request|as per the instruction)\b", section or "", re.I):
        return True
    objective_tokens = _word_tokens(objective)
    section_tokens = _word_tokens(section)
    if len(section_tokens) >= 10:
        objective_text = " ".join(objective_tokens)
        for index in range(len(section_tokens) - 9):
            if " ".join(section_tokens[index : index + 10]) in objective_text:
                return True
    if len(section_tokens) < 8 or len(objective_tokens) < 6:
        return False
    objective_grams = {
        tuple(objective_tokens[index : index + 6])
        for index in range(len(objective_tokens) - 5)
    }
    grams = [tuple(section_tokens[index : index + 6]) for index in range(len(section_tokens) - 5)]
    if len(grams) < 3:
        return False
    hits = sum(1 for gram in grams if gram in objective_grams)
    return hits / len(grams) >= 0.6


def covered_items(objective: str) -> list[str]:
    """The parts a service sentence lists together, such as a comma-and list."""
    brief = service_brief(objective).lower()
    match = re.search(
        r"\b([a-z]{4,}(?: [a-z]{4,})?(?:, [a-z]{4,}(?: [a-z]{4,})?)+,? and [a-z]{4,}(?: [a-z]{4,})?)\b",
        brief,
    )
    if not match:
        return []
    items: list[str] = []
    for part in re.split(r",\s*|\s+and\s+", match.group(1)):
        words = [word for word in part.split() if word not in _GENERIC_TERMS and len(word) >= 4]
        if not words:
            continue
        label = words[-1]
        if label in _GENERIC_TERMS or label in items:
            continue
        items.append(label)
    return items if len(items) >= 2 else []


def _item_present(item: str, text: str) -> bool:
    stem = item[:-1] if item.endswith("s") and len(item) > 4 else item
    return bool(re.search(rf"\b{re.escape(stem)}s?\b", text or "", re.I))


def _pricing_parts(body: str, objective: str) -> str:
    items = covered_items(objective)
    if len(items) < 2:
        return ""
    missing = [item for item in items if not _item_present(item, body)]
    if missing:
        return "Pricing does not label an assumption for every part of the service: " + ", ".join(missing) + "."
    unlabeled = []
    lines = [line for line in (body or "").splitlines() if line.strip()] or [body or ""]
    for item in items:
        labeled = False
        for line in lines:
            if not _item_present(item, line):
                continue
            lowered = line.lower()
            if "assumption" in lowered or re.search(rf"\b{re.escape(item)}\s*:", lowered):
                labeled = True
                break
        if not labeled:
            unlabeled.append(item)
    if unlabeled:
        return "Pricing does not label an assumption for every part of the service: " + ", ".join(unlabeled) + "."
    return ""


def _pound_amounts(text: str) -> list[str]:
    """Pound amounts. A comma after the number is punctuation, not part of it."""
    return re.findall(r"£\s*\d[\d,]*(?:\.\d+)?", text or "")


def _assumption_sentence(sentence: str) -> bool:
    return bool(re.search(r"\bassumption\b", sentence or "", re.I))


def _pound_supported(amount: str, blob: str) -> bool:
    number = re.sub(r"[£\s,]", "", amount or "")
    if not number:
        return False
    compact = re.sub(r",", "", blob or "")
    return bool(re.search(rf"£\s*{re.escape(number)}(?!\d)", compact))


def _ungrounded_amount(body: str, blob: str, allow_assumption: bool = True) -> bool:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        if allow_assumption and _assumption_sentence(sentence):
            continue
        if any(not _pound_supported(amount, blob) for amount in _pound_amounts(sentence)):
            return True
    return False


def _host_words_match(sentence: str, token: str) -> bool:
    """A site is one word, or a run of capitalised words.

    'coffee machine rentals' is a description. 'Joe's Doors' is the site.
    """
    words = re.findall(r"[A-Za-z0-9]+(?:['’]s)?", sentence or "")
    words = [word for word in words if re.sub(r"[^a-z0-9]", "", word.lower()) not in {"and", "or"}]
    for start, word in enumerate(words):
        piece = re.sub(r"[^a-z0-9]", "", word.lower())
        if piece == token:
            return True
        if not word[:1].isupper():
            continue
        joined = ""
        for later in words[start:]:
            if not later[:1].isupper():
                break
            joined += re.sub(r"[^a-z0-9]", "", later.lower())
            if len(joined) > len(token):
                break
            if joined == token:
                return True
    return False


def _names_opened_page(sentence: str, blob: str) -> bool:
    """True when the sentence names a site from an opened page."""
    lowered = (sentence or "").lower()
    for host in re.findall(r"https?://([^/\s]+)", blob or "", re.I):
        host = host.lower().split(":")[0]
        if host in lowered:
            return True
        for label in host.split("."):
            if label in {"www", "co", "uk", "com", "org", "net"}:
                continue
            token = re.sub(r"[^a-z0-9]", "", label)
            if len(token) < 6:
                continue
            if _host_words_match(sentence, token):
                return True
    return False


def _unlinked_price(body: str, blob: str, *, assumptions: bool = True) -> str:
    """A £ figure has to name the opened page it came from.

    A line labelled Assumption may state a figure that is not on a page. A competitor
    price is a citation, so that label does not remove the page name.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        if "£" not in sentence:
            continue
        if assumptions and _assumption_sentence(sentence):
            continue
        if not _pound_amounts(sentence):
            continue
        if not _names_opened_page(sentence, blob):
            return "The section states a £ figure without naming the opened page it came from."
    return ""


def _example_leak(text: str, objective: str, blob: str) -> str:
    """A copied example, or a trade this request and the opened pages do not name."""
    lowered = (text or "").lower()
    if any(phrase in lowered for phrase in _EXAMPLE_PHRASES):
        return "The section uses an example that is not this request."
    haystack = f"{objective}\n{blob}".lower()
    for word in _EXAMPLE_WORDS:
        if re.search(rf"\b{word}\b", lowered) and not re.search(rf"\b{word}\b", haystack):
            return "The section uses an example that is not this request."
    return ""


def component_price_snippets(evidence: list[dict] | None, objective: str) -> list[tuple[str, str]]:
    """The opened sentence the pricing check counts for each part of the service.

    Empty when that part has no £ figure on an opened page. This is the snippet
    to show the model. It is not a sentence to store.
    """
    rows: list[tuple[str, str]] = []
    for item in covered_items(objective):
        found: list[str] = []
        for page in evidence or []:
            if not isinstance(page, dict) or len(found) >= 2:
                continue
            url = str(page.get("source_url") or page.get("url") or "")
            title = str(page.get("source_title") or page.get("title") or "page")
            text = str(page.get("extracted_content") or page.get("text") or "")
            blob = " ".join(part for part in (url, title, text) if part)
            if not _item_priced(item, blob):
                continue
            sentence = ""
            for part in re.split(r"(?<=[.!?])\s+|\n+", blob):
                if not re.search(r"£\s*[\d,]+", part):
                    continue
                if _item_present(item, part) or _item_present(item, title):
                    sentence = re.sub(r"\s+", " ", part).strip()
                    break
            if not sentence:
                continue
            if len(sentence) > 400:
                at = sentence.find("£")
                start = max(0, at - 180)
                sentence = sentence[start : start + 400]
            where = f"{title} ({url})" if url else title
            found.append(f"{where}: {sentence}")
        rows.append((item, " | ".join(found)))
    return rows


def _item_priced(item: str, blob: str) -> bool:
    """True when a £ figure is for this part.

    A door price that says 'including handles' does not price the handles.
    """
    stem = item[:-1] if item.endswith("s") and len(item) > 4 else item
    item_re = re.compile(rf"\b{re.escape(stem)}s?\b", re.I)
    aside = re.compile(
        r"\b(?:including|include|includes|included|such as|whether|depending|depends|along with|as well as)\b",
        re.I,
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", blob or ""):
        if not item_re.search(sentence) or not re.search(r"£\s*[\d,]+", sentence):
            continue
        if re.search(
            rf"£\s*[\d,]+(?:\.\d+)?(?:\s*(?:-|–|to)\s*£?\s*[\d,]+(?:\.\d+)?)?(?:\s+\w+){{0,4}}\s+(?:per|for|a|an)\s+(?:\w+\s+){{0,3}}{re.escape(stem)}s?\b",
            sentence,
            re.I,
        ):
            return True
        for match in item_re.finditer(sentence):
            before = sentence[max(0, match.start() - 48) : match.start()]
            if aside.search(before):
                continue
            after = aside.split(sentence[match.end() :], maxsplit=1)[0]
            if re.search(r"£\s*[\d,]+", after):
                return True
    return False


def _false_not_stated(body: str, blob: str, objective: str) -> str:
    """'Not stated' is false when an opened sentence already prices that part."""
    if not re.search(r"£\s*[\d,]+", blob or ""):
        return ""
    items = covered_items(objective)
    for line in (body or "").splitlines():
        if not re.search(r"\bnot stated\b|\bno opened page\b|\bno price\b|\bno figure\b", line, re.I):
            continue
        if re.search(r"£\s*[\d,]+", line):
            continue
        named = [item for item in items if _item_present(item, line)]
        priced = [item for item in named if _item_priced(item, blob)]
        if priced:
            return "Pricing says " + ", ".join(priced) + " is not stated, but an opened page states a £ figure for it."
        if not named:
            return "Pricing says a figure is not stated, but an opened page states a £ figure."
    return ""


def measurements_still_needed(objective: str) -> list[str]:
    """What a quote still needs, taken from the parts this request names.

    A £ figure on a page is a market reference until those measurements are known.
    The words come from the request. Nothing here is a stored sentence.
    """
    items = covered_items(objective)
    if len(items) < 2:
        return []
    needed: list[str] = []
    if re.search(r"\bkitchen\b", service_brief(objective), re.I):
        needed.append("kitchen size")
    for item in items:
        stem = item[:-1] if item.endswith("s") and len(item) > 4 else item
        if stem == "door":
            needed.extend(["door count", "door size"])
        elif "worktop" in item:
            needed.extend(["worktop length", "worktop material"])
        elif stem == "handle":
            needed.append("handle count")
        else:
            needed.extend([f"{item} count", f"{stem} size"])
    unique: list[str] = []
    for item in needed:
        if item not in unique:
            unique.append(item)
    return unique


_CLAIM_STOP = {
    "assumption", "priced", "pricing", "charging", "charge", "charges", "charged",
    "offer", "offers", "offered", "offering", "service", "services", "page", "pages",
    "stated", "states", "state", "similar", "like", "competitor", "competitors",
    "starts", "starting", "start", "from", "with", "that", "this", "they", "their",
    "only", "also", "such", "than", "into", "about", "around", "based", "company",
    "opened", "figure", "amount", "price", "prices", "market", "reference",
    "plan", "planning", "your", "them", "then", "have", "been", "will", "would",
    "could", "should", "which", "where", "when", "what", "each",
}
_EXCLUSION_PHRASES = (
    "pay on completion",
    "excluding vat",
    "plus vat",
    "week (minimum)",
    "fitting not included",
    "fitting included",
    "not included",
    "supply only",
    "excluding",
)


def _unit_next_to_amount(text: str, number: str) -> str:
    """The unit written beside this £ figure. A later 'per day' about cups is not that unit."""
    patterns = (
        (r"/\s*weeks?\b|\bper\s+weeks?\b|\ba\s+weeks?\b", "per week"),
        (r"/\s*days?\b|\bper\s+days?\b|\ba\s+days?\b", "per day"),
        (r"/\s*months?\b|\bper\s+months?\b|\ba\s+months?\b", "per month"),
        (r"\bper\s+(?:standard\s+)?doors?\b", "per door"),
        (r"\bper\s+hours?\b", "per hour"),
    )
    for match in re.finditer(r"£\s*([\d,]+(?:\.\d+)?)", text or ""):
        value = re.sub(r",", "", match.group(1))
        if value != (number or ""):
            continue
        window = (text or "")[match.end() : match.end() + 48]
        for pattern, label in patterns:
            if re.search(pattern, window, re.I):
                return label
        return ""
    return ""


def _units_in(text: str) -> set[str]:
    """Units a price sentence actually states. A page that says nothing of the kind has none."""
    raw = text or ""
    found: set[str] = set()
    if re.search(
        r"per\s+square\s+(?:metre|meter)s?\b|per\s+sq\.?\s*m\b|per\s+m(?:2|²)\b|/\s*m(?:2|²)\b|/\s*sqm\b",
        raw,
        re.I,
    ):
        found.add("per square metre")
    elif re.search(r"per\s+linear\s+(?:metre|meter)s?\b", raw, re.I):
        found.add("per linear metre")
    elif re.search(r"per\s+(?:metre|meter)s?\b", raw, re.I):
        found.add("per metre")
    if re.search(r"per\s+(?:standard\s+|kitchen\s+)?doors?\b", raw, re.I):
        found.add("per door")
    if re.search(r"per\s+weeks?\b|/\s*weeks?\b|\ba\s+weeks?\b", raw, re.I):
        found.add("per week")
    if re.search(r"per\s+months?\b", raw, re.I):
        found.add("per month")
    if re.search(r"per\s+hours?\b", raw, re.I):
        found.add("per hour")
    if re.search(r"per\s+days?\b", raw, re.I):
        found.add("per day")
    if re.search(r"£\s*\d[\d,]*(?:\.\d+)?\s+each\b", raw, re.I):
        found.add("each")
    return found


def _word_in_site_name(word: str, source: str) -> bool:
    """'builders' is the site bestbuilders.co.uk, even though the body never splits it."""
    if len(word or "") < 4:
        return False
    for host in re.findall(r"https?://([^/\s]+)", source or "", re.I):
        parts = [re.sub(r"[^a-z0-9]", "", part) for part in host.lower().split(".")]
        labels = [part for part in parts if part and part not in {"www", "co", "uk", "com", "org", "net"}]
        if not labels:
            continue
        label = labels[0]
        if len(label) < len(word) + 3:
            continue
        if label.startswith(word) or label.endswith(word):
            return True
        if word.endswith("s") and len(word) > 4 and (label.startswith(word[:-1]) or label.endswith(word[:-1])):
            return True
    return False


def _word_on_page(word: str, source: str) -> bool:
    """Whole word, plus a trailing s. The page title and the site name count."""
    if re.search(rf"\b{re.escape(word)}\b", source or "", re.I):
        return True
    if word.endswith("s") and re.search(rf"\b{re.escape(word[:-1])}\b", source or "", re.I):
        return True
    if not word.endswith("s") and re.search(rf"\b{re.escape(word)}s\b", source or "", re.I):
        return True
    return _word_in_site_name(word, source)


def _content_words(phrase: str) -> list[str]:
    words = []
    for word in re.findall(r"[a-z]{4,}", (phrase or "").lower()):
        if word in _CLAIM_STOP or word in words:
            continue
        words.append(word)
    return words


def _object_phrases(sentence: str) -> list[str]:
    phrases = []
    after = re.compile(
        r"£\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:-|–|to)\s*£?\s*\d[\d,]*(?:\.\d+)?)?"
        r"(?:\s+per\s+(?:square\s+|linear\s+|standard\s+|kitchen\s+)?[\w²]+)?"
        r"(?:\s+each)?\s+(?:for|of)\s+([^.;,\n]+)",
        re.I,
    )
    before = re.compile(
        r"([A-Za-z0-9'][^.£\n]{0,80}?)\s+(?:starts?\s+at|priced\s+at|charg(?:e|es|ing)|costs?)\s+£",
        re.I,
    )
    price_of = re.compile(r"((?:[A-Za-z]{3,}\s+){1,3})prices?\s+of\s+£", re.I)
    for match in after.finditer(sentence or ""):
        phrases.append(match.group(1))
    for match in before.finditer(sentence or ""):
        phrases.append(" ".join(_content_words(match.group(1))[-4:]))
    for match in price_of.finditer(sentence or ""):
        phrases.append(match.group(1))
    return phrases


def _title_before_url(text: str) -> str:
    """A single short line in front of a URL is that page's title. A price paragraph is not."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) != 1:
        return ""
    line = lines[0]
    if "£" in line or len(line) > 140 or line.lower().startswith("http"):
        return ""
    return line


def _pages_in_blob(blob: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"https?://[^\s)>\"]+", blob or "", re.I))
    if not matches:
        return [("", blob or "")]
    pages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        url = match.group(0).rstrip(".,)")
        previous = matches[index - 1].end() if index else 0
        title = _title_before_url(blob[previous : match.start()])
        pages.append((url, f"{url} {title} {blob[match.end():end]}".strip()))
    return pages


def _cited_source(sentence: str, blob: str) -> str:
    """The opened page this sentence names, or the one page that states the same £ figure."""
    pages = _pages_in_blob(blob)
    named = [text for url, text in pages if url and _names_opened_page(sentence, url)]
    amounts = _pound_amounts(sentence)
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        holders = [text for text in named if any(_pound_supported(amount, text) for amount in amounts)]
        return holders[0] if len(holders) == 1 else ("\n".join(holders or named))
    holders = [text for _url, text in pages if any(_pound_supported(amount, text) for amount in amounts)]
    if not holders and not any(url for url, _text in pages):
        if any(_pound_supported(amount, blob) for amount in amounts):
            return blob or ""
        return ""
    if len(holders) == 1:
        return holders[0]
    return "\n".join(holders)


def _claimed_numbers(sentence: str) -> list[str]:
    numbers = [re.sub(r"[£\s,]", "", amount) for amount in _pound_amounts(sentence)]
    for extra in re.findall(
        r"£\s*\d[\d,]*(?:\.\d+)?\s*(?:-|–|to)\s*£?\s*(\d[\d,]*(?:\.\d+)?)",
        sentence or "",
    ):
        numbers.append(re.sub(r",", "", extra))
    return numbers


def _number_on_page(number: str, source: str) -> bool:
    """£10 is not £10.60, and it is not the '.10' in £19.10. M25 is not £25."""
    compact = re.sub(r",", "", source or "")
    if "." in (number or ""):
        return bool(re.search(rf"(?<![A-Za-z\d]){re.escape(number)}(?!\d)", compact))
    return bool(re.search(rf"(?<![A-Za-z\d.]){re.escape(number)}(?!\d)(?!\.\d)", compact))


def _price_span(source: str, sentence: str) -> str:
    amounts = _pound_amounts(sentence)
    spans = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", source or ""):
        if any(_pound_supported(amount, chunk) for amount in amounts):
            spans.append(chunk)
    text = " ".join(spans) if spans else (source or "")
    if len(text) > 500:
        at = text.find("£")
        start = max(0, at - 220)
        text = text[start : start + 500]
    return text


def _mismatch_against(sentence: str, source: str) -> str:
    """Compare one claim with one opened page."""
    for number in _claimed_numbers(sentence):
        if not _number_on_page(number, source):
            return f"The claim states {number}, and the opened page does not."
        claim_unit = _unit_next_to_amount(sentence, number)
        page_unit = _unit_next_to_amount(source, number)
        if claim_unit and page_unit and claim_unit != page_unit:
            return f"The claim gives the unit '{claim_unit}', and the opened page states '{page_unit}'."
    missing_units = _units_in(sentence) - _units_in(source)
    if missing_units:
        unit = sorted(missing_units)[0]
        return f"The claim gives the unit '{unit}', and the opened page does not."
    for word in ("vinyl", "sample", "wrap"):
        if re.search(rf"\b{word}\b", sentence or "", re.I) and not _word_on_page(word, source):
            return f"The claim gives the product '{word}', and the opened page does not."
    missing_words = []
    for phrase in _object_phrases(sentence):
        for word in _content_words(phrase):
            if word in missing_words or _word_on_page(word, source):
                continue
            missing_words.append(word)
    if missing_words:
        shown = ", ".join(f"'{word}'" for word in missing_words[:4])
        return f"The claim uses {shown}, and the opened page does not."
    span = _price_span(source, sentence).lower()
    claim = (sentence or "").lower()
    for phrase in _EXCLUSION_PHRASES:
        if phrase in span and phrase not in claim:
            if phrase == "pay on completion" and "on completion" in claim:
                continue
            return f"The opened page states '{phrase}' with the £ figure. The claim does not."
    return ""


def _page_text(blob: str, url: str) -> str:
    for page_url, text in _pages_in_blob(blob):
        if page_url == url:
            return text
    return ""


def _separate_price_claims(sentence: str, blob: str) -> list[str]:
    """Two priced claims joined by while or by and. A range such as £19 and £96 stays one claim."""
    parts = re.split(r"\b(?:while|whereas)\b", sentence or "", flags=re.I)
    claims: list[str] = []
    for part in parts:
        bits = re.split(r"\band\b", part, flags=re.I)
        if (
            len(bits) >= 2
            and all(_pound_amounts(bit) for bit in bits)
            and all(_names_opened_page(bit, blob) for bit in bits)
        ):
            claims.extend(bit.strip() for bit in bits)
        else:
            claims.append(part.strip())
    priced = [claim for claim in claims if claim and _pound_amounts(claim)]
    return priced


def _match_one_claim(sentence: str, blob: str, *, require_named: bool) -> str:
    """One £ claim against the one page it names."""
    named = [url for url, _text in _pages_in_blob(blob) if url and _names_opened_page(sentence, url)]
    if not named:
        holders: list[str] = []
        numbers = _claimed_numbers(sentence)
        for number in numbers:
            for url, text in _pages_in_blob(blob):
                if url and _number_on_page(number, text) and url not in holders:
                    holders.append(url)
        if len(holders) > 1 and len(numbers) > 1:
            co_located = False
            for url in holders:
                text = _page_text(blob, url)
                if all(_number_on_page(number, text) for number in numbers):
                    co_located = True
                    break
            if not co_located:
                return "The claim mixes prices from more than one opened page. One claim, one source."
        if require_named:
            number = _claimed_numbers(sentence)[0]
            return f"The claim states {number}, and it does not say which opened page that figure is on."
        if len(holders) == 1:
            return _mismatch_against(sentence, _page_text(blob, holders[0]))
        return ""
    if len(named) > 1:
        hosts = {re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower() for url in named}
        if len(hosts) == 1:
            holders = [
                url for url in named
                if any(_pound_supported(amount, _page_text(blob, url)) for amount in _pound_amounts(sentence))
            ]
            if len(holders) == 1:
                return _mismatch_against(sentence, _page_text(blob, holders[0]))
            if holders:
                return _mismatch_against(sentence, "\n".join(_page_text(blob, url) for url in holders))
            number = _claimed_numbers(sentence)[0]
            return f"The claim states {number}, and the opened page does not."
        pieces = re.split(r"\b(?:and|while|whereas)\b", sentence or "", flags=re.I)
        clauses: list[str] = []
        for piece in pieces:
            # A host tag after the price stays with that price. "doors and worktops" stays one title.
            # A later business, with its own opened page and no £, is not part of this price.
            if clauses and not _pound_amounts(piece):
                piece_hosts = {
                    re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower()
                    for url in named
                    if _names_opened_page(piece, url)
                }
                price_hosts = {
                    re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower()
                    for url in named
                    if _names_opened_page(clauses[-1], url)
                }
                if (
                    piece_hosts
                    and price_hosts
                    and piece_hosts.isdisjoint(price_hosts)
                    and _IDENTITY.search(piece)
                ):
                    clauses.append(piece)
                    continue
                clauses[-1] = clauses[-1] + " and " + piece
                continue
            if clauses and _pound_amounts(piece) and not any(_names_opened_page(piece, url) for url in named):
                clauses[-1] = clauses[-1] + " and " + piece
            else:
                clauses.append(piece)
        saw_price = False
        for clause in clauses:
            if not _pound_amounts(clause):
                continue
            saw_price = True
            pages = [url for url in named if _names_opened_page(clause, url)]
            page_hosts = {re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower() for url in pages}
            if len(page_hosts) != 1:
                number = _claimed_numbers(clause)[0]
                return f"The claim states {number}, and it does not say which opened page that figure is on."
            holders = [
                url for url in pages
                if any(_pound_supported(amount, _page_text(blob, url)) for amount in _pound_amounts(clause))
            ]
            source = _page_text(blob, holders[0]) if len(holders) == 1 else "\n".join(
                _page_text(blob, url) for url in (holders or pages)
            )
            reason = _mismatch_against(clause, source)
            if reason:
                return reason
        if saw_price:
            return ""
    source = _cited_source(sentence, blob)
    if not source:
        return ""
    return _mismatch_against(sentence, source)


def claim_mismatch(sentence: str, blob: str) -> str:
    """The quantity, the unit, or the product is not what the opened page says.

    An Assumption line that does not name a page may state a planning figure.
    A line that names a page has to use that page's quantity, unit, and words.
    Two prices in one sentence are two claims. Each names its own page.
    """
    if not _pound_amounts(sentence or ""):
        return ""
    claims = _separate_price_claims(sentence, blob)
    if len(claims) > 1:
        for claim in claims:
            reason = _match_one_claim(claim, blob, require_named=True)
            if reason:
                return reason
        return ""
    return _match_one_claim(sentence, blob, require_named=False)


def _claim_mismatches(body: str, blob: str) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        reason = claim_mismatch(sentence, blob)
        if reason:
            return reason
    return ""


def _missing_measurements(body: str, objective: str) -> str:
    """A found £ figure is a market reference until the request's measurements are named."""
    if "£" not in (body or "") or not _pound_amounts(body):
        return ""
    needed = measurements_still_needed(objective)
    if not needed:
        return ""
    lowered = (body or "").lower()
    missing = [item for item in needed if item not in lowered]
    if not missing:
        return ""
    return (
        "A £ figure on an opened page is a market reference, not a price for this service, until the size or the spec is known. "
        "Name what still has to be measured: " + ", ".join(missing) + "."
    )


def _competitor_prices(body: str, blob: str) -> str:
    if not re.search(r"£\s*\d", blob or ""):
        return ""
    if _ungrounded_amount(body, blob, allow_assumption=False) or not _pound_amounts(body):
        if not _pound_amounts(body):
            return "The competitor section does not cite a £ figure from an opened page."
        return "The competitor section cites a £ figure that is not on an opened page."
    return _unlinked_price(body, blob, assumptions=False)


def _sells_avoided(body: str, objective: str) -> bool:
    """The offer or the advert must not sell the alternative the request says to avoid."""
    if not re.search(r"\bavoid(?:s|ing)? a full\b", objective or "", re.I):
        return False
    return bool(re.search(r"\bfull (?:kitchen )?replacement (?:service|quote)\b", body or "", re.I))


def _rate_card(body: str) -> str:
    """Three or more prices in one sentence are a pasted tariff, not an offer.

    A small, medium, and large package, or a per-unit range, is one price.
    A dash-separated menu is still a tariff when it is not a package.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        menu = len(re.findall(r"-\s*£\s*\d", sentence))
        if len(_pound_amounts(sentence)) < 3 and menu < 3:
            continue
        if re.search(r"\bpackages?\b", sentence, re.I):
            continue
        if re.search(r"\b(?:per\s+[a-z]+|a\s+(?:session|week|day))\b", sentence, re.I):
            continue
        return "The section pastes a price list. State one offer in a sentence."
    return ""


def _pasted_page(body: str) -> str:
    """A raw page title or an opened-source dump is not a section."""
    if re.search(r"(?m)^\s*opened sources\s*$", body or "", re.I):
        return "The section pastes opened page text. Keep that in the evidence panel."
    if re.search(r"(?m)^\s*URL:\s*\S", body or "", re.I):
        return "The section pastes opened page text. Keep that in the evidence panel."
    if re.search(r"\s\|\s", body or ""):
        return "The section pastes a page title. State what the page says in a sentence."
    return ""


def _assumption_on_fact(body: str) -> str:
    """Assumption is the method. A cited fact stays on its own line."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        if not re.search(r"\bassumption\b", sentence, re.I):
            continue
        if re.search(r"\[F\d+\]", sentence, re.I) or re.search(r"""\bstates\s+["'“]""", sentence, re.I):
            return (
                "An Assumption line restates a cited fact. "
                "Keep the fact on its own line and use Assumption for the method."
            )
    return ""


def _headline_problem(title: str) -> str:
    """A headline is a complete phrase under ten words, and it does not start with Ask about."""
    if re.search(r"\bcost guide\b", title or "", re.I) or len(title or "") > 80:
        return "The headline copies a page title. Write a short promise."
    words = re.findall(r"[A-Za-z0-9'’%]+", title or "")
    if words and (title.strip().lower().startswith("ask about") or len(words) >= 10 or len(words) < 3):
        return "The headline is not a complete phrase under 10 words."
    # "Without a Full Refit" is a prepositional fragment. A headline names the result.
    if re.match(r"^(?:without|with|for|from|by|through|via|about|over|under)\b", (title or "").strip(), re.I):
        if not re.search(
            r"\b(?:replace|replacing|refresh|rent|hire|install|avoid|keep|get|book|save|upgrade|transform|paint|call|contact)\b",
            title or "",
            re.I,
        ):
            return "The headline is a fragment, not a complete phrase under 10 words."
    return ""


_OFFER_VERB = re.compile(
    r"\b(offers?|hires?|hiring|rents?|rental|sells?|supply|supplies|installs?|charges?|package|services?|respray|paints?|painting|replaces?|replacement)\b",
    re.I,
)
_HOST_IN_TEXT = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:co\.uk|com|org|net|example)\b", re.I)
_NOT_COMPETITOR = {
    "the offer", "this service", "opened pages", "opened sources", "market research",
    "next steps", "target customer", "call to action", "the customer", "the problem",
    "the plan", "the owner", "united kingdom",
}
_WHEN = re.compile(
    r"\b(first week|first month|this week|this month|within a week|within a month)\b",
    re.I,
)


def _guide_host(host: str, blob: str) -> bool:
    """A cost-guide URL is not a competitor."""
    host = (host or "").lower()
    if not host:
        return False
    for url in re.findall(r"https?://[^\s)>\"]+", blob or "", re.I):
        if host in url.lower() and guide_page(url, ""):
            return True
    return False


def _competitor_names(body: str, blob: str = "") -> list[str]:
    """Named businesses whose sentence also says what they offer."""
    found: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
        if _GUIDE_TITLE.search(sentence):
            continue
        if not (_pound_amounts(sentence) or _OFFER_VERB.search(sentence)):
            continue
        names: list[str] = []
        hosts: list[str] = []
        for match in _IDENTITY.finditer(sentence):
            phrase = re.sub(r"\s+", " ", match.group(0)).strip()
            if phrase.lower() in _NOT_COMPETITOR or _GUIDE_TITLE.search(phrase):
                continue
            if market_report_page("", phrase):
                continue
            names.append(phrase.lower())
        for match in re.finditer(
            r"\b([A-Z][A-Za-z'’]{4,})\s+(?:provides|offers|offer|charges|sells|rents|replaces)\b",
            sentence,
        ):
            token = match.group(1).lower().replace("’", "'")
            if token in {"coffee", "kitchen", "service", "rental", "machine", "doors"}:
                continue
            if any(token == existing.replace("’", "'").split()[-1] for existing in names):
                continue
            if token not in _NOT_COMPETITOR and token not in names:
                names.append(token)
        for host in _HOST_IN_TEXT.findall(sentence):
            if market_report_page(host, "") or _guide_host(host, blob):
                continue
            hosts.append(host.lower())
        chosen = names[:2] or hosts[:1]
        for name in chosen:
            if any(_same_business(name, kept) for kept in found):
                continue
            if name not in found:
                found.append(name)
    return found


def _same_business(left: str, right: str) -> bool:
    """Two labels for one company, such as Refresh Kitchen and Refresh Your Kitchen."""
    stop = {"your", "the", "and", "uk"}
    a = [word for word in (left or "").split() if word not in stop]
    b = [word for word in (right or "").split() if word not in stop]
    if not a or not b or a[0] != b[0]:
        return False
    return set(a) <= set(b) or set(b) <= set(a)


def _shared_channel(body: str) -> bool:
    """The sentence the engine used to store for every trade."""
    return bool(re.search(r"posts on the website in the first week,? and phones", body or "", re.I))


def _shared_steps(body: str) -> bool:
    """The plan the engine used to store for every trade."""
    return bool(re.search(
        r"phones two suppliers and writes down the published price"
        r"|phones two rental companies and writes down the weekly price"
        r"|phones two businesses and writes down what a home visit"
        r"|phones one office to ask which"
        r"|phones one customer to book a (?:home )?visit",
        body or "",
        re.I,
    ))


def _pasted_quote(body: str, blob: str) -> str:
    """A run of the opened page's words is a paste. The model has to paraphrase."""
    page_words = re.findall(r"[a-z0-9']+", (blob or "").lower())
    span = 12
    if len(page_words) < span:
        return ""
    sequences = {" ".join(page_words[index:index + span]) for index in range(len(page_words) - (span - 1))}
    body_words = re.findall(r"[a-z0-9']+", (body or "").lower())
    for index in range(len(body_words) - (span - 1)):
        if " ".join(body_words[index:index + span]) in sequences:
            return "The competitor line pastes the page. Paraphrase it and keep the citation."
    return ""


def _offer_names_measurement(body: str, objective: str) -> bool:
    """A package offer says what the visit measures. The words come from the request."""
    needed = measurements_still_needed(objective)
    if not needed:
        return True
    lowered = (body or "").lower()
    if any(item in lowered for item in needed):
        return True
    return bool(re.search(r"\bmeasur", lowered) and any(_item_present(item, body) for item in covered_items(objective)))


def _generic_offer(body: str, objective: str) -> bool:
    """One sentence that names the parts and a contrast, and nothing the visit measures."""
    if len(covered_items(objective)) < 2 and not measurements_still_needed(objective):
        return False
    compact = re.sub(r"\s+", " ", (body or "")).strip()
    return bool(re.fullmatch(
        r"The offer is a package covering .+, and it differs from .+\.?",
        compact,
        re.I,
    ))


_PACKAGE_METHOD = re.compile(
    r"\b(?:price each|priced from|price the|per door|per standard door|price is determined|determined by the number|calculated based on|based on the number|price is based on)\b",
    re.I,
)
_SHOUTED_HEADING = re.compile(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,}){2,}\b")


def _assumption_states_method(body: str, objective: str) -> bool:
    """A package price names how the measured inputs become a price."""
    if len(covered_items(objective)) < 2 and not measurements_still_needed(objective):
        return True
    lines = [line for line in (body or "").splitlines() if "assumption" in line.lower()]
    if not lines:
        return True
    return any(_PACKAGE_METHOD.search(line) for line in lines)


def _offer_differs(body: str, objective: str) -> bool:
    """A contrast word counts only when the request did not already use it."""
    for marker in _CONTRAST:
        if not re.search(rf"\b{re.escape(marker)}", body or "", re.I):
            continue
        if re.search(rf"\b{re.escape(marker)}", objective or "", re.I):
            continue
        return True
    return False


def _section_shape(key: str, body: str, objective: str, blob: str = "") -> str:
    """Missing elements. Empty when this section has the element it needs."""
    lowered = (body or "").lower()
    if key == "target customer":
        if "responsible for the cost" in lowered or not any(_has_word(lowered, word) for word in _BUYERS):
            return "The section does not name a customer segment."
    elif key == "problem":
        if not any(_has_word(lowered, cue) for cue in _PROBLEM_BURDEN):
            return "The section does not state a concrete problem."
    elif key == "offer and positioning":
        if _sells_avoided(body, objective):
            return "The section describes the alternative as the service."
        if not _offer_differs(body, objective):
            return "The section does not state what is sold and how it differs."
        unlinked = _unlinked_price(body, blob)
        if unlinked:
            return unlinked
        missing_package = [item for item in covered_items(objective) if not _item_present(item, body)]
        if len(covered_items(objective)) >= 2 and missing_package:
            return "The offer does not name the package: " + ", ".join(missing_package) + "."
        if _generic_offer(body, objective):
            return "The offer is one generic sentence. Name what the package includes and what the visit measures."
        if not _offer_names_measurement(body, objective):
            return "The offer does not say what the visit measures."
    elif key == "channels":
        if _shared_channel(body):
            return "The section uses the shared form. Name an action that fits this business."
        if re.search(r"three lines|headline\s*:|call to action\s*:", lowered):
            return "The section describes the advert format instead of a channel."
        if re.search(r"\bindustry reports?\b|\bonline platforms?\b", lowered):
            return "The section does not name a channel, a practical action, and a timeframe."
        named = [word for word in _CHANNELS if _has_word(lowered, word)]
        actions = [word for word in _OWNER_ACTIONS if _has_action(lowered, word) and word not in named]
        if not named:
            return "The section does not name a channel."
        if not actions:
            return "The channel section does not name a practical action."
        if not _WHEN.search(body or ""):
            return "The channel section does not name a timeframe."
    elif key == "next steps":
        if _shared_steps(body):
            return "The section uses the shared form. Name an action that fits this business."
        if "first practical action" in lowered or not any(_has_word(lowered, word) for word in _OWNER_ACTIONS):
            return "The next step repeats the service instead of an action the owner takes."
        if "first week" not in lowered or "first month" not in lowered:
            return "The next steps are not a prioritised plan. Name the action for the first week and the action for the first month."
    elif key == "pricing":
        missing = _pricing_parts(body, objective)
        if missing:
            return missing
        unlinked = _unlinked_price(body, blob)
        if unlinked:
            return unlinked
        absent = _false_not_stated(body, blob, objective)
        if absent:
            return absent
        omitted = _pricing_omits_page_figure(body, blob)
        if omitted:
            return omitted
        missing_measure = _missing_measurements(body, objective)
        if missing_measure:
            return missing_measure
        if "£" in (body or "") and "assumption" not in lowered:
            return "Pricing has no assumption. State the method, with no new £ figure."
        if not _assumption_states_method(body, objective):
            return "Pricing lists references and does not state how to turn the measurements into a price."
        return ""
    elif key == "competitor and market research":
        if re.search(r"(?m)^\s*" + _SHOUTED_HEADING.pattern, body or ""):
            return "The competitor line starts with a page heading. Paraphrase it and keep the citation."
        pasted_quote = _pasted_quote(body, blob)
        if pasted_quote:
            return pasted_quote
        priced = _competitor_prices(body, blob)
        if priced:
            return priced
        if len(_competitor_names(body, blob)) < 2:
            return "The competitor section names fewer than two competitors and what each offers."
        if not _offer_differs(body, objective):
            return "The competitor section does not say how this service differs."
        return ""
    elif key == "call to action":
        named_owner = names_requester(body, objective)
        if named_owner:
            return named_owner
        if _sells_avoided(body, objective):
            return "The call to action asks for the alternative the service avoids."
        if not _ASK.search(lowered):
            return "The call to action does not ask the customer to take a step."
        if len(lowered.strip()) < 20:
            return "The call to action is too short to use."
    elif key == "advert":
        headline = re.search(r"headline\s*:\s*(.+)", body or "", re.I)
        title = headline.group(1).strip() if headline else ""
        problem = _headline_problem(title)
        if problem:
            return problem
        if _sells_avoided(body, objective):
            return "The call to action asks for the alternative the service avoids."
        match = re.search(r"call to action\s*:\s*(.+)", body or "", re.I)
        cta = match.group(1) if match else ""
        if cta and _confirm_request(cta):
            return "The call to action asks the reader to confirm the request."
        if cta and not _ASK.search(cta):
            return "The call to action does not ask the customer to take a step."
    if key in {"offer and positioning", "advert", "call to action", "next steps"}:
        unlinked = _unlinked_price(body, blob)
        if unlinked:
            return unlinked
    return ""


def _invented_service_area(text: str, objective: str) -> bool:
    """A named or chosen service area is invented when the request forbids one."""
    if not re.search(r"do not invent a service area", objective or "", re.I):
        return False
    if not re.search(r"\bservice areas?\b", text or "", re.I):
        return False
    if re.search(r"\b(unknown|unresolved|not stated|not known|do not invent|no service area|limitation|boundar)\b", text or "", re.I):
        return False
    return True


def _invented_saving(text: str, blob: str) -> bool:
    """A saving is a claim. It has to be on an opened page."""
    if not re.search(r"\bsave(?:s|d)?\b|\bsaving\b", text or "", re.I):
        return False
    return not re.search(r"\bsave(?:s|d)?\b|\bsaving\b", blob or "", re.I)


def _without_service_area(text: str, objective: str) -> str:
    """Drop a sentence that invents a service area. A stated gap stays."""
    if not re.search(r"do not invent a service area", objective or "", re.I):
        return text or ""
    kept = []
    for part in re.split(r"(?<=[.!?])\s+|;\s*", text or ""):
        piece = part.strip()
        if not piece:
            continue
        if re.search(r"\bservice areas?\b", piece, re.I) and not re.search(
            r"\b(unknown|unresolved|not stated|not known|do not invent|no service area|limitation|boundar)\b",
            piece,
            re.I,
        ):
            continue
        kept.append(piece if re.search(r"[.!?]$", piece) else piece + ".")
    return " ".join(kept)


def _unsupported_percent(text: str, blob: str) -> bool:
    compact = re.sub(r"\s+", "", blob or "")
    for percent in re.findall(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?\s*%", text or ""):
        token = re.sub(r"\s+", "", percent)
        if token not in compact and percent not in (blob or ""):
            return True
    return False


def quote_draft(text: str) -> str:
    """The model's words, marked so a later check cannot mistake them for a passed plan."""
    lines = ["> " + line if line.strip() else ">" for line in (text or "").splitlines()]
    return "\n".join(lines).strip()


def retained_draft(
    model_text: str,
    objective: str,
    blob: str = "",
    reasons: dict | None = None,
    sections: dict | None = None,
) -> str:
    """The repaired sections, one reason per failed check, and the full draft.

    A check marked passed is the section that was kept. A one-shot fragment is not
    reported as passed when a later repair replaced it. This text is not a passed plan.
    """
    from_text = parse_sections(model_text or "")
    repaired = sections or {}
    lines = [
        "Validation rejected this draft. No check was relaxed.",
        "",
    ]
    failed = 0
    for key in _REQUIRED:
        reason = (reasons or {}).get(key) or ""
        body = (repaired.get(key) or "").strip()
        if not reason:
            if body:
                reason = section_rejection(key, body, blob, objective)
            else:
                reason = section_rejection(key, from_text.get(key) or "", blob, objective)
                body = (from_text.get(key) or "").strip()
        if reason:
            failed += 1
            lines.append(f"[{key}] {reason}")
            continue
        lines.append(f"[{key}] passed")
        if body:
            lines.append(quote_draft(body))
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
    if not plan_sections_filled(findings or "", blob, objective):
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
        cleaned[key] = prose
    draft = _format_sections(cleaned, evidence)
    if not plan_sections_filled(draft, blob, objective):
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
        lines.append("")
    if "nothing was sent" not in "\n".join(lines).lower():
        lines.append("Nothing was sent. No purchase was made.")
    return "\n".join(lines).strip() + "\n"
