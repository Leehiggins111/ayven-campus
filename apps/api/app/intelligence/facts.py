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


def extract_facts(evidence: list[dict] | None) -> list[dict]:
    """One row per price sentence. A page with no £ figure contributes its title only."""
    facts: list[dict] = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or item.get("url") or "")
        title = str(item.get("source_title") or item.get("title") or "")
        text = str(item.get("extracted_content") or item.get("text") or "")
        if not url.startswith("http"):
            continue
        entity = _entity(title, url)
        priced = [sentence for sentence in _sentence_list(text) if _MONEY.search(sentence)]
        chosen = priced[:2] or ([title] if title else [])
        for sentence in chosen:
            amount = _MONEY.search(sentence)
            unit = _UNIT.search(sentence)
            included = _INCLUDED.search(sentence)
            facts.append({
                "id": f"F{len(facts) + 1}",
                "source": url,
                "entity": entity,
                "quote": re.sub(r"\s+", " ", sentence).strip()[:280],
                "quantity": amount.group(0) if amount else "",
                "unit": unit.group(0) if unit else "",
                "included": included.group(0).strip() if included else "",
                "relevance": "unlabeled",
            })
            if len(facts) >= 12:
                return facts
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
        if fact["relevance"] == "not relevant":
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


def _foreign_names(sentence: str, objective: str, facts: list[dict]) -> list[str]:
    from .deliverable import _IDENTITY

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
            continue
        quoted = " ".join((fact.get("entity") or "") + " " + (fact.get("quote") or "") for fact in cited).lower()
        if _matches(sentence, cited, objective) and all(name.lower() in quoted for name in named):
            kept.append(sentence)
        else:
            repaired = _repair(cited)
            if repaired:
                kept.append(repaired)
    return "\n".join(kept).strip()
