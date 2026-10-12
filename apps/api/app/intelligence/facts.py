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
    r"\bper\s+[a-z]{3,}(?:\s+[a-z]{3,}){0,2}|\b(?:a|an)\s+(?:door|week|month|day|session|hour|year|metre|meter)\b|\beach\s+(?:door|week|month|day|session|hour)\b",
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


_GENERIC_TITLE = re.compile(
    r"\b(replacement|cost|guide|how much|price|prices|worktops|services|service|home|installed|welcome)\b",
    re.I,
)


def _entity(title: str, url: str, text: str = "") -> str:
    """The business name on the page, not the article headline."""
    parts = [re.sub(r"\s+", " ", part).strip(" -|") for part in re.split(r"\s*[|\-–—]\s*", title or "")]
    proper = []
    for part in parts:
        if not part or not re.search(r"[A-Za-z]", part):
            continue
        if re.fullmatch(r"[\d\s+().-]+", part) or "£" in part:
            continue
        if _GENERIC_TITLE.search(part):
            continue
        if _proper_name(part):
            proper.append(part)
    if proper:
        return proper[-1][:80]
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


def _fact_row(url: str, title: str, sentence: str, text: str = "") -> dict:
    amount = _MONEY.search(sentence or "")
    unit = _UNIT.search(sentence or "")
    included = _INCLUDED.search(sentence or "")
    return {
        "source": url,
        "entity": _entity(title, url, text),
        "quote": re.sub(r"\s+", " ", sentence or "").strip()[:280],
        "quantity": amount.group(0) if amount else "",
        "unit": unit.group(0) if unit else "",
        "included": included.group(0).strip() if included else "",
        "relevance": "unlabeled",
    }


def _bare_price(sentence: str) -> bool:
    """A figure whose sentence does not already say what it buys."""
    if re.search(r"\b(per\s+[a-z]+|small|medium|large|full groom|a week)\b", sentence or "", re.I):
        return False
    words = re.findall(r"[A-Za-z]+", sentence or "")
    if len(words) >= 12 and re.search(
        r"\b(transform|replace|rent|hire|install|package|cost|charge|from|week|door|machine)\b",
        sentence or "",
        re.I,
    ):
        return False
    return True


_OFFER_HINT = re.compile(
    r"\b(offers?|replace|replaced|replacing|replacement|rents?|rental|hires?|installs?|supplies|services?|packages?|transform)\b",
    re.I,
)
_NOT_A_NAME = re.compile(
    r"\b(machine|machines|rental|leasing|buying|grinder|grinders|commercial|cost|guide|replacement|online|wide)\b",
    re.I,
)
_DIRECTORY = re.compile(
    r"\b(lists|directory|compare local|browse|near me|near you|find a|find trusted|finding a|are ready)\b",
    re.I,
)
_LOUD_PROMISE = re.compile(r"24-hour|24/7|free home visit", re.I)


def _price_table(fact: dict) -> bool:
    """A row of materials and prices is not one offer."""
    return len(re.findall(r"£\s*\d", fact.get("quote") or "")) >= 5


def _spec_sheet(fact: dict) -> bool:
    """A machine's dimensions are not the offer."""
    quote = fact.get("quote") or ""
    return bool(re.search(r"\d+\s*w\s*x\s*\d+", quote, re.I))


def _rate_card_quote(fact: dict) -> bool:
    from .deliverable import _rate_card

    return bool(_rate_card(fact.get("quote") or ""))


def _repair_service(fact: dict) -> bool:
    """A repair specialist is not a competitor for a replacement service."""
    blob = f"{fact.get('entity') or ''} {fact.get('quote') or ''}"
    return bool(re.search(r"\brepairs?\b", blob, re.I))


def _proper_name(entity: str) -> bool:
    """A business name, not a page headline about the product."""
    if not entity or entity.islower() or "£" in entity:
        return False
    words = entity.split()
    if not 1 <= len(words) <= 4 or _NOT_A_NAME.search(entity):
        return False
    if any(re.fullmatch(r"[A-Za-z]", word) for word in words):
        return False
    plain = re.findall(r"[A-Za-z']+", entity)
    generic = {"mobile", "dog", "grooming", "groom", "price", "prices", "service", "services", "cost", "costs", "uk", "the"}
    if plain and all(word.lower() in generic for word in plain):
        return False
    return True


def _sentence_names_business(sentence: str) -> bool:
    from .deliverable import _IDENTITY

    for match in _IDENTITY.finditer(sentence or ""):
        words = re.sub(r"\s+", " ", match.group(0)).strip().split()
        while words and (words[0].lower() in {"at", "the", "for", "and", "with"} or _NOT_A_NAME.search(words[0])):
            words = words[1:]
        if words and _proper_name(" ".join(words)):
            return True
        if len(words) >= 2 and _proper_name(" ".join(words[-2:])):
            return True
    return False


def _offer_sentence(text: str) -> str:
    """One sentence from the page that says what the business does. Not a title and not a price."""
    fallback = ""
    offer = ""
    named_offer = ""
    for sentence in _sentence_list(text):
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if len(sentence) < 32 or len(sentence) > 240 or _MONEY.search(sentence):
            continue
        if sentence.count(" - ") >= 2 or sentence.count(" | ") >= 1:
            continue
        words = re.findall(r"[A-Za-z][A-Za-z']*", sentence)
        caps = sum(1 for word in words if word[:1].isupper())
        if len(words) >= 8 and caps / len(words) >= 0.45:
            continue
        if _DIRECTORY.search(sentence):
            continue
        prose = bool(re.search(r"\b(we|our|your|with|without|you|is|are|who|I)\b", sentence))
        if _OFFER_HINT.search(sentence) and prose:
            names = _sentence_names_business(sentence)
            specific = re.search(r"\b(rent|rental|hire|hiring|replac)", sentence, re.I)
            if names and specific:
                return sentence
            if names and not named_offer:
                named_offer = sentence
                continue
            if not offer:
                offer = sentence
            continue
        if not fallback:
            fallback = sentence
    return named_offer or offer or fallback


def extract_facts(evidence: list[dict] | None) -> list[dict]:
    """Price sentences first, then what a business offers. A guide is not a competitor."""
    from .deliverable import guide_page, listing_page, market_report_page

    business_priced: list[dict] = []
    offers: list[dict] = []
    other_priced: list[dict] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or item.get("url") or "")
        title = str(item.get("source_title") or item.get("title") or "")
        text = str(item.get("extracted_content") or item.get("text") or "")
        if not url.startswith("http") or market_report_page(url, title, text) or listing_page(url):
            continue
        directory = bool(_DIRECTORY.search(f"{title}\n{text[:1500]}"))
        business = not guide_page(url, title) and not directory
        sentences = _sentence_list(text)
        found = [
            sentence for sentence in sentences
            if _MONEY.search(sentence) and not re.search(r"\bdog walking\b|\bindividual walk\b", sentence, re.I)
        ]
        bucket = business_priced if business else other_priced
        if found:
            for sentence in found[:2]:
                index = sentences.index(sentence)
                nxt = sentences[index + 1].strip() if index + 1 < len(sentences) else ""
                if (
                    nxt
                    and len(nxt) <= 180
                    and not _MONEY.search(nxt)
                    and re.search(r"\b(buying|renting|rental|hire|hiring)\b", nxt, re.I)
                    and not re.search(r"\b(buying|renting|rental|hire|hiring)\b", sentence, re.I)
                ):
                    sentence = sentence.rstrip() + " " + nxt
                row = _fact_row(url, title, sentence, text)
                if not row["unit"] and _bare_price(row["quote"]):
                    row["quote"] = (
                        row["quote"].rstrip(".")
                        + ", and the page text does not give the size, the material, or the unit"
                    )[:280]
                row["title_only"] = False
                row["business"] = business
                row["directory"] = directory
                bucket.append(row)
        elif title and _MONEY.search(title):
            amount = _MONEY.search(title).group(0)
            row = _fact_row(
                url,
                title,
                f"{amount}, and the page text does not give the size, the material, or the unit.",
                text,
            )
            row["quantity"] = amount
            row["title_only"] = True
            row["business"] = business
            row["directory"] = directory
            bucket.append(row)
        elif business:
            sentence = _offer_sentence(text)
            if not sentence:
                continue
            row = _fact_row(url, title, sentence, text)
            row["title_only"] = False
            row["business"] = True
            row["directory"] = False
            offers.append(row)
    ordered = (
        [row for row in business_priced if row.get("unit")]
        + [row for row in business_priced if not row.get("unit")]
        + [row for row in other_priced if row.get("unit")]
        + offers
        + [row for row in other_priced if not row.get("unit")]
    )
    facts = []
    for row in ordered[:12]:
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
    held_back = []
    for fact in facts:
        fact["relevance"] = labels.get(fact["id"], "unlabeled")
        if fact["relevance"] == "not relevant" and not fact.get("quantity"):
            if fact.get("business"):
                held_back.append(fact)
            continue
        kept.append(fact)
    have = len([fact for fact in kept if fact.get("business")])
    for fact in held_back:
        if have >= 2:
            break
        kept.append(fact)
        have += 1
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


def _clip_quote(quote: str, limit: int = 220) -> str:
    """One short sentence. Keep the start when the £ figure is already near it."""
    quote = re.sub(r"\s+", " ", quote or "").strip().strip('"').rstrip(".")
    quote = (
        quote.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
        .replace("·", " ")
    )
    quote = "".join(ch if ord(ch) < 256 or ch == "£" else " " for ch in quote)
    quote = quote.replace("?", " ").replace("!", " ")
    quote = re.sub(r"\s+", " ", quote).strip(" ,")
    headed = re.match(
        r"^(?:[A-Z][A-Za-z']+\s+){2,6}(?:&\s+[A-Z][A-Za-z']+\s+)?(?P<rest>At\s+[A-Z].{40,})",
        quote,
    )
    if headed:
        quote = headed.group("rest")
    quote = re.sub(r"\bwhy\b\s+\S+(?:\s+\S+){0,6}\s*$", "", quote, flags=re.I).strip(" ,")
    if "does not give the size" in quote or len(quote) <= limit:
        return quote
    at = quote.find("£")
    if at > 100:
        start = quote.rfind(" ", 0, max(0, at - 40))
        quote = quote[start + 1 if start >= 0 else 0 :].strip()
    if len(quote) <= limit:
        return quote
    cut = quote[:limit].rfind(" ")
    return quote[: cut if cut > 40 else limit].strip()


def _first_band(quote: str) -> str:
    """A dash-separated tariff keeps the first priced offer. A package list stays whole."""
    if quote.count(" - ") < 3 or len(re.findall(r"£\s*\d", quote)) < 3:
        return quote
    if re.search(r"\bpackages?\b", quote, re.I):
        return quote
    match = re.search(r"£\s*\d[\d,]*(?:\.\d+)?", quote)
    if not match:
        return quote
    head = quote[: match.end()]
    parts = head.split(": ")
    if len(parts) >= 3:
        head = ": ".join(parts[-2:])
    elif len(parts) == 2 and len(parts[0]) > 60:
        head = parts[1]
    return head.strip(" -:,")


def _without_spec(quote: str) -> str:
    """A machine's dimensions and option menu are not the offer. Keep the price."""
    if not re.search(r"\d+\s*w\s*x\s*\d+", quote or "", re.I) and (quote or "").count(" - ") < 3:
        return quote
    cut = (quote or "").find(" - ")
    head = quote[:cut] if cut > 20 else quote
    head = re.sub(r"\d+\s*w\s*x\s*\d+.*", "", head, flags=re.I)
    return head.strip(" -:,")


def _market_line(fact: dict) -> str:
    """The opened quote, the site, and the citation, as one sentence. No amount is added."""
    quote = _clip_quote(_without_spec(_first_band(fact.get("quote") or "")))
    host = _host(fact.get("source") or "")
    cite = f"[{fact['id']}]"
    entity = re.sub(r"\s+", " ", fact.get("entity") or "").strip()
    shown = host.removeprefix("www.") if host else ""
    prefix = ""
    if entity and _proper_name(entity) and entity.lower() not in quote.lower():
        prefix = f"{entity} ({shown}) " if shown else f"{entity} "
    elif shown and shown not in quote.lower():
        prefix = f"({shown}) "
    return f"{prefix}{quote} {cite}.".strip()


def name_the_page(text: str, facts: list[dict] | None) -> str:
    """A cited £ sentence names the opened host. The amount stays the fact's own."""
    kept = []
    for sentence in _sentence_list(text):
        hosts = []
        for fact in _cited(sentence, facts or []):
            host = _host(fact.get("source") or "")
            shown = host.removeprefix("www.")
            if not host or host in sentence.lower() or (shown and shown in sentence.lower()) or "£" not in sentence:
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


def _pricing_section(text: str, facts: list[dict] | None, objective: str, *, skip_title_only: bool = False) -> str:
    """Up to three opened £ lines, then one Assumption. The model's worksheet is not reused.

    A copied request, a page title, and a new £ figure are not the section.
    """
    from .deliverable import covered_items, measurements_still_needed

    priced = [fact for fact in (facts or []) if "£" in (fact.get("quantity") or "")]
    priced.sort(key=lambda fact: (0 if fact.get("unit") else 1, 0 if fact.get("business") else 1, 1 if fact.get("title_only") else 0))
    lines = []
    seen = set()
    for fact in priced:
        if len(lines) >= 3:
            break
        if (
            fact.get("directory")
            or _price_table(fact)
            or _rate_card_quote(fact)
            or (skip_title_only and fact.get("title_only"))
        ):
            continue
        line = _market_line(fact)
        if " | " in line or not line:
            continue
        key = _host(fact.get("source") or "")
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    items = covered_items(objective)
    needed = measurements_still_needed(objective)
    lowered = (objective or "").lower()
    if items or needed:
        detail = []
        if items:
            detail.append(", ".join(items))
        if needed:
            detail.append("what still has to be measured: " + ", ".join(needed))
        assumption = "Assumption: " + " — ".join(detail) + "."
    elif re.search(r"\brent", lowered):
        assumption = "Assumption: the weekly price is set after seeing the site and which machine is wanted."
    elif "kennel" in lowered:
        assumption = "Assumption: the price is set after seeing the animal."
    else:
        assumption = f"Assumption: the price for {_service_topic(objective)} is set after measuring."
    lines.append(assumption)
    return "\n".join(lines).strip()


def _service_topic(objective: str) -> str:
    from .deliverable import anchor_terms

    skip = {"replaces", "replace", "replaced", "owner", "groom", "company", "service", "business"}
    nouns = [term for term in anchor_terms(objective) if term not in skip]
    return " ".join(nouns[:2]) or "the service"


def _cover_phrase(objective: str) -> str:
    from .deliverable import covered_items

    items = covered_items(objective)
    if items:
        return ", ".join(items)
    topic = _service_topic(objective)
    if "-" in topic or topic.startswith(("a ", "the ")):
        return topic
    return f"a {topic}"


def _advert_body(objective: str) -> str:
    lowered = (objective or "").lower()
    if re.search(r"\brent", lowered):
        method = "the weekly price is set after seeing the site"
    elif "kennel" in lowered:
        method = "the price is set after seeing the animal"
    else:
        method = "the price is set after measuring"
    return f"The offer covers {_cover_phrase(objective)}, and {method}."


def _service_headline(objective: str, blob: str = "") -> str:
    """A complete phrase from the request's own words. Not a page title and not 'Ask about'."""
    from .deliverable import borrowed_service_name, _headline_problem

    topic = _service_topic(objective)
    for headline in (f"Measure {topic}, then set the price", "A visit, then a written scope"):
        if _headline_problem(headline) or (blob and borrowed_service_name(headline, objective, blob)):
            continue
        return headline
    return "A visit, then a written scope"


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
    if not headline and not body:
        only = re.sub(r"\s+", " ", (text or "")).strip()
        if only and "nothing was sent" not in only.lower() and len(only.splitlines()) == 1 and len(only.split()) >= 3:
            headline = only
    if not headline and not body:
        return text
    if not body or "£" in body or re.search(r"upfront cost|free home", body or "", re.I):
        body = _advert_body(objective)
    if borrowed_service_name(headline, objective, blob) or _bad_headline(headline) or _LOUD_PROMISE.search(headline) or re.search(r"\bno upfront\b|\bupfront costs?\b", headline or "", re.I):
        headline = _service_headline(objective, blob)
    if borrowed_service_name(body, objective, blob):
        body = _advert_body(objective)
    if (
        not cta
        or not _ASK.search(cta)
        or _PROMISE_LINE.search(cta)
        or re.search(r"upfront cost|free home", cta or "", re.I)
        or borrowed_service_name(cta, objective, blob)
    ):
        cta = "Contact us and ask about a visit."
    rebuilt = f"Headline: {headline}\nBody: {body}\nCall to action: {cta}"
    if section_rejection("advert", rebuilt, blob, objective):
        return text
    return rebuilt


def _repair_service_line(objective: str) -> str:
    """The request's own service. A name taken from an opened page is not used."""
    phrase = _cover_phrase(objective)
    lowered = (objective or "").lower()
    if re.search(r"\brent", lowered):
        return f"The service rents {phrase} instead of selling one."
    if "kennel" in lowered:
        return f"The service provides {phrase} at the customer's home rather than in a kennel."
    if re.search(r"\bfull (?:kitchen )?replacement\b", lowered):
        return f"The service replaces {phrase}, and it avoids a full replacement."
    return f"The service provides {phrase}."


def _repair_problem(objective: str) -> str:
    """The burden named in the request, when the model described the market instead."""
    lowered = (objective or "").lower()
    topic = _service_topic(objective)
    if "kennel" in lowered:
        return f"The problem is that a kennel is inconvenient compared with {topic} at home."
    if re.search(r"\bfull (?:kitchen )?replacement\b", lowered):
        return f"The problem is the cost and disruption of a full replacement for {topic}."
    return f"The problem is the cost and the inconvenience of buying {_cover_phrase(objective)} outright."


def _bad_headline(headline: str) -> bool:
    from .deliverable import _headline_problem

    return bool(_headline_problem(headline))


def _thin_channel(text: str) -> bool:
    """The same 'ask for a reply' line is not a practical action."""
    lowered = (text or "").lower()
    if "asks for a reply" not in lowered:
        return False
    return not re.search(r"\b(phone|phones|visit|measure|book)\b", lowered)


def _channel_sentence(objective: str) -> str:
    topic = _service_topic(objective)
    lowered = (objective or "").lower()
    if "kitchen" in lowered:
        return (
            "The owner posts on the website in the first week and phones two suppliers "
            "to ask what a replacement door and a worktop cost."
        )
    if re.search(r"\brent", lowered):
        return (
            "The owner posts on the website in the first week and phones one office "
            f"to ask which {topic} they want to rent."
        )
    if "kennel" in lowered:
        return (
            "The owner posts on the website in the first week and phones one customer "
            "to book a home visit."
        )
    return (
        f"The owner posts on the website in the first week and phones one customer "
        f"to book a visit about {topic}."
    )


def _repair_channels(text: str, objective: str, blob: str) -> str:
    from .deliverable import section_rejection

    if not section_rejection("channels", text, blob, objective) and not _thin_channel(text):
        return text
    replaced = _channel_sentence(objective)
    if section_rejection("channels", replaced, blob, objective):
        return text
    return replaced


def _repair_next_steps(objective: str) -> str:
    from .deliverable import measurements_still_needed

    topic = _service_topic(objective)
    lowered = (objective or "").lower()
    needed = measurements_still_needed(objective)
    measure = ", the ".join(needed[:3]) if needed else "what the visit has to count"
    if "kitchen" in lowered:
        return (
            "In the first week the owner phones two suppliers and writes down the published price "
            "for the doors and the worktops. "
            f"In the first month the owner visits one kitchen and measures the {measure}."
        )
    if re.search(r"\brent", lowered):
        return (
            f"In the first week the owner phones two rental companies and writes down the weekly price they publish for a {topic}. "
            "In the first month the owner visits one office and sets the price after seeing the site."
        )
    if "kennel" in lowered:
        return (
            "In the first week the owner phones two businesses and writes down what a home visit includes. "
            "In the first month the owner visits one customer and sets the price after seeing the animal."
        )
    return (
        f"In the first week the owner phones two suppliers and writes down the published offer for {topic}. "
        "In the first month the owner visits one customer and sets the price after measuring."
    )


def _repair_offer(objective: str) -> str:
    package = _cover_phrase(objective)
    lowered = (objective or "").lower()
    if re.search(r"\bfull (?:kitchen )?replacement\b", lowered):
        contrast = "a full replacement"
    elif "kennel" in lowered:
        contrast = "a kennel"
    elif re.search(r"\brent", lowered):
        contrast = "buying the machine outright"
    else:
        contrast = "the longer alternative"
    return f"The offer is a package covering {package}, and it differs from {contrast}."


def _difference(objective: str) -> str:
    topic = _service_topic(objective)
    lowered = (objective or "").lower()
    if re.search(r"\bfull (?:kitchen )?replacement\b", lowered):
        return f"This service differs by replacing the {topic} rather than a full replacement."
    if "kennel" in lowered:
        return "This service differs by working at the customer's home rather than in a kennel."
    if re.search(r"\brent", lowered):
        return f"This service differs by renting the {topic} rather than selling one outright."
    return f"This service differs by selling {topic} rather than the longer alternative."


def _competitor_lines(text: str, facts: list[dict] | None, objective: str, blob: str) -> str:
    """Two opened businesses and what each offers. No invented name and no page dump."""
    from .deliverable import section_rejection

    businesses = [
        fact for fact in (facts or [])
        if fact.get("business") and fact.get("quote") and not fact.get("title_only")
        and not _price_table(fact) and not _repair_service(fact)
        and not _DIRECTORY.search(fact.get("quote") or "")
        and not _rate_card_quote(fact)
    ]
    priced = [fact for fact in businesses if fact.get("quantity")]
    others = [fact for fact in businesses if not fact.get("quantity")]
    named = [
        fact for fact in others
        if _OFFER_HINT.search(fact.get("quote") or "") and (_proper_name(fact.get("entity") or "") or _sentence_names_business(fact.get("quote") or ""))
    ]
    named.sort(key=lambda fact: (
        1 if _LOUD_PROMISE.search(fact.get("quote") or "") else 0,
        0 if re.search(r"\b(we supply|we install|we replace|we offer|packages? (?:start|usually))\b", fact.get("quote") or "", re.I) else 1,
        0 if re.search(r"\b(rent|rental|hire|hiring|replac)", fact.get("quote") or "", re.I) else 1,
        1 if (fact.get("quote") or "").lower().startswith("welcome") else 0,
        0 if _proper_name(fact.get("entity") or "") else 1,
    ))

    from .deliverable import anchor_terms

    anchors = [term for term in anchor_terms(objective) if len(term) >= 4]

    def _mentions_service(fact: dict) -> bool:
        blob = f"{fact.get('entity') or ''} {fact.get('quote') or ''}".lower()
        return any(term in blob for term in anchors)

    preferred = [fact for fact in priced if _mentions_service(fact)]
    pool = preferred or priced
    first = next((fact for fact in pool if fact.get("unit")), pool[0] if pool else None)
    candidates = named + [fact for fact in priced if fact is not first] + others
    candidates.sort(key=lambda fact: (fact.get("quote") or "").lower().startswith("welcome"))
    seconds = []
    for fact in candidates:
        if first and _host(fact.get("source") or "") == _host(first.get("source") or ""):
            continue
        if any(_host(fact.get("source") or "") == _host(item.get("source") or "") for item in seconds):
            continue
        seconds.append(fact)
        if len(seconds) >= 4:
            break
    if first is None:
        if len(seconds) < 2:
            return text
        first, seconds = seconds[0], seconds[1:]
    for second in seconds:
        lines = [_market_line(first), _market_line(second)]
        if any(" | " in line for line in lines):
            continue
        if "£" not in "\n".join(lines):
            extra = _opened_price_line(facts, {_host(first.get("source") or ""), _host(second.get("source") or "")})
            if extra:
                lines.append(extra)
        lines.append(_difference(objective))
        rebuilt = "\n".join(lines)
        if not section_rejection("competitor and market research", rebuilt, blob, objective):
            return rebuilt
    return text


def _opened_price_line(facts: list[dict] | None, used_hosts: set[str]) -> str:
    """One opened £ when the two businesses did not state a price."""
    priced = [
        fact for fact in (facts or [])
        if "£" in (fact.get("quantity") or "")
        and not fact.get("directory")
        and not fact.get("title_only")
        and not _price_table(fact)
        and not _spec_sheet(fact)
    ]
    priced.sort(key=lambda fact: (0 if fact.get("unit") else 1, 0 if fact.get("business") else 1))
    for fact in priced:
        if _host(fact.get("source") or "") in used_hosts:
            continue
        line = _market_line(fact)
        if line and "£" in line and " | " not in line:
            return line
    return ""


def _cites_directory(text: str, facts: list[dict] | None) -> bool:
    """A directory page is not a competitor that sells the service."""
    lowered = (text or "").lower()
    for fact in facts or []:
        if not fact.get("directory"):
            continue
        host = _host(fact.get("source") or "").removeprefix("www.")
        if host and host in lowered:
            return True
    return False


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
        if key == "channels" and _thin_channel(named):
            better = _repair_channels(named, objective, blob)
            if better and not section_rejection(key, better, blob, objective):
                return better
        if key == "competitor and market research" and (
            re.search(r"copying those offers", named or "", re.I) or _cites_directory(named, facts)
        ):
            better = _competitor_lines(named, facts, objective, blob)
            if better and better != named and not section_rejection(key, better, blob, objective):
                return better
        if key == "advert" and (
            re.search(r"\bno upfront\b|\bupfront costs?\b", named or "", re.I)
            or not re.search(r"(?im)^headline\s*:", named or "")
            or not re.search(r"(?im)^body\s*:", named or "")
        ):
            better = _repair_advert(named, objective, blob)
            if better and better != named and not section_rejection(key, better, blob, objective):
                return better
        if key == "next steps" and not re.search(r"\b(phone|phones|visit|measure)\b", named or "", re.I):
            better = _repair_next_steps(objective)
            if better and not section_rejection(key, better, blob, objective):
                return better
        return named
    repaired = named
    lowered = reason.lower()
    if key == "pricing":
        repaired = _pricing_section(named, facts, objective)
        if section_rejection(key, repaired, blob, objective):
            without_titles = _pricing_section(named, facts, objective, skip_title_only=True)
            if without_titles and not section_rejection(key, without_titles, blob, objective):
                repaired = without_titles
    elif key == "competitor and market research":
        repaired = _competitor_lines(named, facts, objective, blob)
    elif "repeats the objective" in lowered:
        from .deliverable import _echoes_objective

        lines = [line for line in named.splitlines() if line.strip() and not _echoes_objective(line, objective)]
        repaired = "\n".join(lines).strip()
    elif key == "problem" and ("concrete problem" in lowered or "promises" in lowered):
        repaired = _repair_problem(objective)
    elif key == "service" and "comes from an opened page" in lowered:
        repaired = _repair_service_line(objective)
    elif key == "advert":
        repaired = _repair_advert(named, objective, blob)
    elif key == "channels":
        repaired = _repair_channels(named, objective, blob)
    elif key == "next steps":
        repaired = _repair_next_steps(objective)
    elif key == "offer and positioning":
        repaired = _repair_offer(objective)
    elif key == "call to action" and "promises" in lowered:
        repaired = f"Contact us and ask about a visit for {_service_topic(objective)}."
    if section_rejection(key, repaired, blob, objective):
        return named
    return repaired
