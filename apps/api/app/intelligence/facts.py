"""A numbered fact table taken from opened pages, and the citation check in front of the gate.

The table is built from the page text. The model may label a fact relevant or not.
A writing prompt receives this table and the request. A sentence that states a number
or a competitor name must cite [F#]. Anything else with a number or a name is removed
before the gate sees it. An opinion may stay, without a number or a name.
"""

from __future__ import annotations

import os
import re

_MONEY = re.compile(
    r"(?:£|GBP)\s*[\d,]+(?:\.\d+)?(?:\s*(?:-|–|to)\s*(?:£|GBP)?\s*[\d,]+(?:\.\d+)?)?",
    re.I,
)
_UNIT = re.compile(
    r"\bper\s+[a-z]{3,}(?:\s+[a-z]{3,}){0,2}|\b(?:a|an)\s+(?:door|week|month|day|session|hour|year|metre|meter)\b|\beach\b",
    re.I,
)
_INCLUDED = re.compile(
    r"\b(?:including|include|includes|included|excluding|exclude|excludes|excluded|pay on completion)[^.]{0,80}",
    re.I,
)
_CITE = re.compile(r"\[F(\d+)\]", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_PROMISES = (
    "24/7",
    "free consultation",
    "free quote",
    "for free",
    "guaranteed",
    "same-day",
    "same day",
    "in one day",
    "always ready",
)
_KEEP = {
    "about", "after", "assumption", "based", "between", "business", "charge", "charged",
    "charges", "cited", "count", "customer", "customers", "differ", "differs", "figure",
    "figures", "kitchen", "known", "market", "measured", "measuring", "method", "opened",
    "owner", "page", "pages", "price", "priced", "prices", "reference", "references",
    "request", "service", "stated", "states", "still", "their", "there", "these", "this",
    "using", "which", "would", "quote",
}


def _entity(title: str, url: str) -> str:
    head = re.split(r"[|\-–]", title or "", maxsplit=1)[0].strip()
    head = re.sub(r"\s+", " ", head)
    if head and len(head) <= 80:
        return head
    host = re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0]
    label = host.lower().removeprefix("www.").split(".")[0]
    return label or "opened page"


def _numbers(text: str) -> list[float]:
    found = []
    for match in _MONEY.finditer(text or ""):
        for piece in re.findall(r"[\d,]+(?:\.\d+)?", match.group(0)):
            try:
                found.append(float(piece.replace(",", "")))
            except ValueError:
                continue
    return found


def _sentence_list(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [part.strip() for part in parts if part.strip()]


def _fact_row(url: str, title: str, sentence: str) -> dict:
    amount = _MONEY.search(sentence or "")
    unit = _UNIT.search(sentence or "")
    included = _INCLUDED.search(sentence or "")
    return {
        "source": url,
        "entity": _entity(title, url),
        "quote": re.sub(r"\s+", " ", sentence or "").strip()[:280],
        "quantity": amount.group(0) if amount else "",
        "unit": unit.group(0) if unit else "",
        "included": included.group(0).strip() if included else "",
        "relevance": "unlabeled",
    }


def extract_facts(evidence: list[dict] | None) -> list[dict]:
    """Price sentences first. A title fills a spare row only after every £ figure is in."""
    priced: list[dict] = []
    titles: list[dict] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or item.get("url") or "")
        title = str(item.get("source_title") or item.get("title") or "")
        text = str(item.get("extracted_content") or item.get("text") or "")
        if not url.startswith("http"):
            continue
        found = [sentence for sentence in _sentence_list(text) if _MONEY.search(sentence)]
        if found:
            for sentence in found[:2]:
                priced.append(_fact_row(url, title, sentence))
        elif title:
            titles.append(_fact_row(url, title, title))
    facts = []
    for row in (priced + titles)[:12]:
        row["id"] = f"F{len(facts) + 1}"
        facts.append(row)
    return facts


def render_fact_table(facts: list[dict] | None) -> str:
    rows = []
    for fact in facts or []:
        rows.append(
            f"[{fact['id']}] entity: {fact.get('entity') or ''} | source: {fact.get('source') or ''} | "
            f"quantity: {fact.get('quantity') or 'none'} | unit: {fact.get('unit') or 'none'} | "
            f"included: {fact.get('included') or 'none'}\n"
            f"quote: {fact.get('quote') or ''}"
        )
    if not rows:
        return "Facts:\nNone.\n"
    return "Facts:\n" + "\n".join(rows) + "\n"


def label_relevance(facts: list[dict], objective: str, complete) -> list[dict]:
    """The model marks each fact relevant or not. A total rejection does not empty the table."""
    if not facts or complete is None:
        return facts
    if os.environ.get("AYVEN_LLM_STUB", "1") != "0":
        return facts
    listing = "\n".join(f"{fact['id']}: {fact.get('quote') or ''}" for fact in facts)
    user = (
        "For each fact write one line, F1 relevant or F1 not relevant. "
        "Relevant means the quote is about the service in the request. "
        "Do not add a fact.\n\n"
        f"Request:\n{(objective or '')[:800]}\n\n{listing}"
    )
    try:
        text, _tokens, _meta = complete(
            "EMPLOYEE",
            "Label each fact. Write one line per fact.",
            user,
            max_tokens=240,
            plain=True,
            plan=True,
        )
    except Exception:
        return facts
    labels: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = re.match(r"F(\d+)\s+(not relevant|relevant)\b", line.strip(), re.I)
        if not match:
            continue
        labels[f"F{match.group(1)}"] = "not relevant" if match.group(2).lower().startswith("not") else "relevant"
    kept = []
    for fact in facts:
        fact["relevance"] = labels.get(fact["id"], "unlabeled")
        if fact["relevance"] == "not relevant" and not fact.get("quantity"):
            continue
        kept.append(fact)
    if not kept:
        return facts
    for index, fact in enumerate(kept, start=1):
        fact["id"] = f"F{index}"
    return kept


def _cited(sentence: str, facts: list[dict]) -> list[dict]:
    by_id = {fact["id"].upper(): fact for fact in facts}
    found = []
    for number in _CITE.findall(sentence or ""):
        fact = by_id.get(f"F{number}")
        if fact and fact not in found:
            found.append(fact)
    return found


def _has_number(sentence: str) -> bool:
    if _MONEY.search(sentence or ""):
        return True
    if re.search(r"\d+(?:\.\d+)?\s*%", sentence or ""):
        return True
    bare = _YEAR.sub(" ", sentence or "")
    return bool(re.search(r"\b\d+(?:\.\d+)?\b", bare))


def _title_line(sentence: str) -> bool:
    """A headline written in title case is not a competitor's name."""
    stripped = re.sub(
        r"^(?:headline|body|call to action|assumption)\s*:\s*",
        "",
        (sentence or "").strip(),
        flags=re.I,
    )
    stripped = stripped.strip(" .\"'")
    words = re.findall(r"[A-Za-z]+(?:['’]s)?", stripped)
    small = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "without"}
    words = [word for word in words if word.lower() not in small]
    if not words or len(words) > 14:
        return False
    return all(word[:1].isupper() for word in words)


def _foreign_names(sentence: str, objective: str, facts: list[dict]) -> list[str]:
    from .deliverable import _IDENTITY

    if _title_line(sentence):
        return []
    allowed = " ".join([objective or ""] + [fact.get("entity") or "" for fact in facts] + [fact.get("quote") or "" for fact in facts]).lower()
    names = []
    for match in _IDENTITY.finditer(sentence or ""):
        phrase = re.sub(r"\s+", " ", match.group(0)).strip()
        if phrase.lower() in allowed:
            continue
        names.append(phrase)
    return names


def _promise_blocked(sentence: str, objective: str, cited: list[dict]) -> bool:
    lowered = (sentence or "").lower()
    objective_l = (objective or "").lower()
    quotes = " ".join((fact.get("quote") or "").lower() for fact in cited)
    for phrase in _PROMISES:
        if phrase not in lowered:
            continue
        if phrase in objective_l or phrase in quotes:
            continue
        return True
    return False


def _derived(value: float, cited_values: list[float]) -> bool:
    if any(abs(value - item) < 0.02 for item in cited_values):
        return True
    for index, left in enumerate(cited_values):
        for right in cited_values[index:]:
            if abs(value - (left + right)) < 0.02 or (right and abs(value - (left * right)) < 0.02):
                return True
    return False


def _matches(sentence: str, cited: list[dict], objective: str) -> bool:
    quotes = " ".join(fact.get("quote") or "" for fact in cited)
    sources = " ".join((fact.get("source") or "") + " " + (fact.get("entity") or "") for fact in cited)
    cited_values = _numbers(quotes)
    for value in _numbers(sentence):
        if not _derived(value, cited_values):
            return False
    allowed = set(re.findall(r"[a-z0-9']+", f"{quotes} {sources} {objective}".lower())) | _KEEP
    for word in re.findall(r"[a-z0-9']+", sentence.lower()):
        if len(word) < 5 or word in allowed or word.startswith("f") and word[1:].isdigit():
            continue
        return False
    for name in _foreign_names(sentence, objective, cited):
        if name.lower() not in quotes.lower():
            return False
    return True


def _repair(cited: list[dict]) -> str:
    lines = []
    for fact in cited:
        quote = (fact.get("quote") or "").strip().strip('"')
        if not quote:
            continue
        lines.append(f"{fact.get('entity') or 'The opened page'} states \"{quote}\" [{fact['id']}].")
    return " ".join(lines)


def enforce_citations(text: str, facts: list[dict] | None, objective: str) -> str:
    """Drop an uncited number or name. Repair a bad citation to the fact's own quote."""
    table = facts or []
    kept = []
    for sentence in _sentence_list(text):
        cited = _cited(sentence, table)
        if _promise_blocked(sentence, objective, cited):
            continue
        named = _foreign_names(sentence, objective, table)
        factual = _has_number(sentence) or bool(named)
        if not factual:
            kept.append(sentence)
            continue
        if not cited:
            # A count is not a market figure. Drop the digits and keep the action.
            if named or _MONEY.search(sentence) or re.search(r"%", sentence):
                continue
            stripped = _YEAR.sub(" ", sentence)
            stripped = re.sub(r"\b\d+(?:\.\d+)?\b", "", stripped)
            stripped = re.sub(r"\s{2,}", " ", stripped).strip(" ,;:-")
            if len(stripped.split()) >= 4:
                kept.append(stripped)
            continue
        quoted = " ".join((fact.get("entity") or "") + " " + (fact.get("quote") or "") for fact in cited).lower()
        if _matches(sentence, cited, objective) and all(name.lower() in quoted for name in named):
            kept.append(sentence)
        else:
            repaired = _repair(cited)
            if repaired:
                kept.append(repaired)
    return "\n".join(kept).strip()


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0].lower()


def _market_line(fact: dict) -> str:
    """The opened quote, the site, and the citation, as one sentence. No amount is added."""
    quote = (fact.get("quote") or "").strip().strip('"').rstrip(".")
    host = _host(fact.get("source") or "")
    cite = f"[{fact['id']}]"
    if host and host not in quote.lower():
        return f"{quote} ({host}) {cite}."
    return f"{quote} {cite}."


def name_the_page(text: str, facts: list[dict] | None) -> str:
    """A cited £ sentence names the opened host. The amount stays the fact's own."""
    kept = []
    for sentence in _sentence_list(text):
        hosts = []
        for fact in _cited(sentence, facts or []):
            host = _host(fact.get("source") or "")
            if not host or host in sentence.lower() or "£" not in sentence:
                continue
            if host not in hosts:
                hosts.append(host)
        if hosts:
            sentence = sentence.rstrip(" .") + f" ({hosts[0]})."
        kept.append(sentence)
    return "\n".join(kept).strip()


_PROMISE_LINE = re.compile(
    r"guarantee|24/7|always ready|free consultation|free quote|same-day|same day|in one day",
    re.I,
)
_HOST = re.compile(
    r"\b(?:https?://\S+|www\.\S+|(?:[a-z0-9-]+\.)+(?:co\.uk|com|org|net))\b",
    re.I,
)


def _append_priced(text: str, facts: list[dict] | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip() and not _PROMISE_LINE.search(line)]
    present = "\n".join(lines).replace(",", "")
    for fact in [fact for fact in (facts or []) if fact.get("quantity")][:2]:
        numbers = [re.sub(r"[£\s,]", "", amount) for amount in re.findall(r"£\s*\d[\d,]*(?:\.\d+)?", fact["quantity"])]
        if numbers and all(number in present for number in numbers):
            continue
        lines.append(_market_line(fact))
        present = "\n".join(lines).replace(",", "")
    return "\n".join(lines[:8]).strip()


def _label_pricing(text: str, objective: str) -> str:
    """An opened £ figure stays a market reference. The method is an Assumption with no new £."""
    from .deliverable import covered_items, measurements_still_needed, _item_present

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not any("assumption" in line.lower() for line in lines):
        lines.append(f"Assumption: the price for {_service_topic(objective)} is set after measuring.")
    body = "\n".join(lines)
    for item in covered_items(objective):
        labeled = False
        for line in lines:
            if _item_present(item, line) and "assumption" in line.lower():
                labeled = True
                break
        if not labeled:
            lines.append(f"Assumption: {item} — not stated as its own figure, set after measuring.")
    body = "\n".join(lines)
    needed = measurements_still_needed(objective)
    if any("£" in line for line in lines) and needed and any(item not in body.lower() for item in needed):
        lines.append("Assumption: what still has to be measured: " + ", ".join(needed) + ".")
    return "\n".join(lines[:8]).strip()


def _service_topic(objective: str) -> str:
    from .deliverable import anchor_terms

    skip = {"replaces", "replace", "replaced", "owner", "groom", "company", "service", "business"}
    nouns = [term for term in anchor_terms(objective) if term not in skip]
    return " ".join(nouns[:2]) or "the service"


def _service_headline(objective: str, blob: str = "") -> str:
    """A short promise from the request's own words, not a page title."""
    from .deliverable import borrowed_service_name

    topic = _service_topic(objective)
    for headline in (f"Ask about {topic}", "Ask about a straightforward visit"):
        if len(headline) < 12:
            continue
        if blob and borrowed_service_name(headline, objective, blob):
            continue
        return headline[:70]
    return "Ask about a straightforward visit"


def _repair_advert(text: str, objective: str, blob: str) -> str:
    from .deliverable import _ASK, borrowed_service_name, owner_section, section_rejection

    def parts(source: str) -> tuple[str, str, str]:
        headline = body = cta = ""
        for line in (source or "").splitlines():
            match = re.match(r"^(headline|body|call to action)\s*:\s*(.*)$", line.strip(), re.I)
            if not match or not match.group(2).strip():
                continue
            label = match.group(1).lower()
            content = match.group(2).strip()
            if label == "headline" and not headline:
                headline = content
            elif label == "body" and not body:
                body = content
            elif label == "call to action" and not cta:
                cta = content
        return headline, body, cta

    headline, body, cta = parts(text)
    cleaned = owner_section("advert", text)
    if cleaned:
        h2, b2, c2 = parts(cleaned)
        headline, body, cta = headline or h2, body or b2, cta or c2
    if not (headline and body):
        return text
    if borrowed_service_name(headline, objective, blob):
        headline = _service_headline(objective, blob)
    if borrowed_service_name(body, objective, blob):
        body = f"The offer covers {_service_topic(objective)}, and the price is set after measuring."
    if not cta or not _ASK.search(cta) or _PROMISE_LINE.search(cta) or borrowed_service_name(cta, objective, blob):
        cta = "Contact us and ask about a visit."
    rebuilt = f"Headline: {headline}\nBody: {body}\nCall to action: {cta}"
    if section_rejection("advert", rebuilt, blob, objective):
        return text
    return rebuilt


def _repair_problem(objective: str) -> str:
    """The burden named in the request, when the model described the market instead."""
    lowered = (objective or "").lower()
    topic = _service_topic(objective)
    if "kennel" in lowered:
        return f"The problem is that a kennel is inconvenient compared with {topic} at home."
    if re.search(r"\bfull (?:kitchen )?replacement\b", lowered):
        return f"The problem is the cost and disruption of a full replacement for {topic}."
    return f"The problem is the cost and the inconvenience of buying {topic} outright."


def _repair_channels(text: str, objective: str, blob: str) -> str:
    from .deliverable import borrowed_service_name, section_rejection

    if not section_rejection("channels", text, blob, objective):
        return text
    replaced = _HOST.sub("a directory", text)
    phrase = borrowed_service_name(replaced, objective, blob)
    if phrase:
        name = re.search(r"'([^']+)'", phrase)
        if name:
            replaced = re.sub(re.escape(name.group(1)), "", replaced, flags=re.I)
            replaced = re.sub(r"\s{2,}", " ", replaced).strip(" ,;:-")
    if section_rejection("channels", replaced, blob, objective):
        return text
    return replaced


def repair_before_gate(key: str, text: str, facts: list[dict] | None, objective: str, blob: str) -> str:
    """Name the page on a cited £, and finish a pricing or advert line the model almost wrote.

    An empty reply stays empty. A promise such as 24/7 is not rewritten here.
    """
    if not (text or "").strip():
        return ""
    if re.search(r"24/7|always ready", text or "", re.I):
        return text
    from .deliverable import section_rejection

    named = name_the_page(text, facts)
    reason = section_rejection(key, named, blob, objective)
    if not reason:
        return named
    repaired = named
    lowered = reason.lower()
    if key in {"competitor and market research", "pricing"} and "£" in reason:
        if key == "competitor and market research":
            named = "\n".join(
                line for line in named.splitlines()
                if line.strip() and not re.search(r"includes none|offers no unit", line, re.I)
            ).strip()
        repaired = _append_priced(named, facts)
        if key == "pricing":
            repaired = _label_pricing(repaired, objective)
    elif key == "pricing" and "assumption" in lowered:
        repaired = _label_pricing(_append_priced(named, facts), objective)
    elif key == "pricing" and "every part" in lowered:
        repaired = _label_pricing(named, objective)
    elif "repeats the objective" in lowered:
        from .deliverable import _echoes_objective

        lines = [line for line in named.splitlines() if line.strip() and not _echoes_objective(line, objective)]
        repaired = "\n".join(lines).strip()
    elif key == "problem" and "concrete problem" in lowered:
        repaired = _repair_problem(objective)
    elif key == "advert":
        repaired = _repair_advert(named, objective, blob)
    elif key == "channels":
        repaired = _repair_channels(named, objective, blob)
    if section_rejection(key, repaired, blob, objective):
        return named
    return repaired
