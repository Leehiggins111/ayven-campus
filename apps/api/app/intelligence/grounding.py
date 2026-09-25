"""Factual-grounding check for model synthesis.

Evidence and deterministic tools set the factual boundaries. The model may
reason inside them. An unsupported external fact is removed before publication.
"""

from __future__ import annotations

import re

from .think import strip_think

_PREFIX = re.compile(
    r"^(FACT|INFERENCE|RECOMMENDATION|UNKNOWN|EVIDENCE-BACKED FACT|DETERMINISTIC RESULT|"
    r"EXPLICIT INFERENCE|UNSUPPORTED FACT)\s*:\s*",
    re.I,
)
_URL = re.compile(r"https?://[^\s)>\]]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_MONEY = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_HEDGE = re.compile(r"\b(typically|usually|may|might|could|possible|plausible|likely|suggests?|worth|consider)\b", re.I)
_REC = re.compile(r"\b(should|recommend|next|verify|confirm|do not|don't|approval|investigate)\b", re.I)


def _sentences(text: str) -> list[str]:
    clean = strip_think(text or "")
    parts = re.split(r"(?<=[.!?])\s+|\n+", clean)
    return [part.strip(" -\t") for part in parts if part.strip(" -\t")]


def _norm_money(raw: str) -> str:
    return raw.replace(",", "")


def _specifics_unsupported(body: str, corpus: str) -> bool:
    lowered = corpus.lower()
    for url in _URL.findall(body):
        if url.rstrip(".,") not in corpus:
            return True
    for email in _EMAIL.findall(body):
        if email.lower() not in lowered:
            return True
    for amount in _MONEY.findall(body):
        if _norm_money(amount) not in corpus.replace(",", "") and amount not in corpus:
            return True
    return False


def _backed(body: str, corpus: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]{4,}", body.lower()) if w not in {"this", "that", "with", "from", "have", "were", "been"}]
    if not words:
        return False
    blob = corpus.lower()
    hit = sum(1 for w in words if w in blob)
    return hit / len(words) >= 0.5


def classify_sentence(sentence: str, evidence: str, deterministic: str) -> str:
    """Return one of the six publication classes."""
    corpus = f"{evidence}\n{deterministic}"
    match = _PREFIX.match(sentence.strip())
    label = match.group(1).upper() if match else ""
    body = _PREFIX.sub("", sentence.strip()).strip() or sentence.strip()
    if _specifics_unsupported(body, corpus):
        return "UNSUPPORTED FACT"
    if label in ("INFERENCE", "EXPLICIT INFERENCE"):
        return "EXPLICIT INFERENCE"
    if label == "RECOMMENDATION":
        return "RECOMMENDATION"
    if label == "UNKNOWN":
        return "UNKNOWN"
    if label == "DETERMINISTIC RESULT":
        return "DETERMINISTIC RESULT" if _backed(body, deterministic) or not _specifics_unsupported(body, corpus) else "UNSUPPORTED FACT"
    if label in ("FACT", "EVIDENCE-BACKED FACT"):
        if _backed(body, evidence):
            return "EVIDENCE-BACKED FACT"
        if _backed(body, deterministic):
            return "DETERMINISTIC RESULT"
        return "UNSUPPORTED FACT"
    if _REC.search(body):
        return "RECOMMENDATION"
    if _HEDGE.search(body):
        return "EXPLICIT INFERENCE"
    if _backed(body, deterministic) and re.search(r"£|\d", body):
        return "DETERMINISTIC RESULT"
    if _backed(body, evidence):
        return "EVIDENCE-BACKED FACT"
    return "EXPLICIT INFERENCE"


def ground_text(text: str, evidence: str = "", deterministic: str = "") -> dict:
    """Drop unsupported facts. Keep labelled inference and recommendations."""
    kept = []
    removed = []
    labels = []
    for sentence in _sentences(text):
        kind = classify_sentence(sentence, evidence, deterministic)
        labels.append(kind)
        body = _PREFIX.sub("", sentence).strip()
        if kind == "UNSUPPORTED FACT":
            removed.append(sentence)
            continue
        kept.append(f"{kind}: {body}")
    return {"text": "\n".join(kept).strip(), "removed": removed, "labels": labels}
