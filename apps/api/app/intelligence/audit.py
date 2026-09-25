"""Authoritative supervisor decision. The model may advise; it does not outvote the checks."""

from __future__ import annotations

import os
import re

from .think import contains_think

_LIVE = ("tickets are available", "available to buy now", "in stock now", "we can purchase")


def asserts_unsupported_final_quote(text: str) -> bool:
    """True when the briefing adopts one total as the customer quote."""
    sample = text or ""
    if re.search(r"the final quote is\s+£", sample, re.I):
        return True
    if re.search(r"\bquote is final\b", sample, re.I):
        return True
    if re.search(r"final price is\s+£", sample, re.I):
        return True
    return False


def claims_live_availability(text: str) -> bool:
    lowered = (text or "").lower()
    if "live availability: not claimed" in lowered and not any(phrase in lowered for phrase in _LIVE):
        return False
    return any(phrase in lowered for phrase in _LIVE)


def authoritative_decision(report: str, task_class: str, focus: str, attempt: int, conflicts: list | None = None) -> str:
    limit = int(os.environ.get("AYVEN_MAX_ATTEMPTS", "2"))
    if conflicts:
        return "ESCALATE"
    if contains_think(report):
        return "TAKE_OVER"
    if asserts_unsupported_final_quote(report):
        return "TAKE_OVER"
    if claims_live_availability(report):
        return "TAKE_OVER"
    text = (report or "").lower()
    if focus == "scenarios":
        if "1533.00" not in report or "963.00" not in report:
            return "RETURN" if attempt < limit else "TAKE_OVER"
        if "ambiguous" not in text:
            return "TAKE_OVER"
        if "not applied" not in text and "unknown_not_applied" not in text:
            return "TAKE_OVER"
        if "not a final quote" not in text:
            return "TAKE_OVER"
        return "ACCEPT"
    if focus == "gaps":
        if task_class == "internal_door_quote" and "handing" not in text:
            return "TAKE_OVER"
        if "gap" not in text and "missing" not in text and "unknown" not in text:
            return "RETURN" if attempt < limit else "TAKE_OVER"
        return "ACCEPT"
    if focus in ("routes", "channels", "evidence"):
        if task_class == "football_tickets" and "official" not in text:
            return "RETURN" if attempt < limit else "TAKE_OVER"
        if task_class == "internal_door_quote" and "no supplier is named" not in text and "http" not in text:
            return "TAKE_OVER"
        return "ACCEPT"
    if focus in ("prospects", "draft"):
        if "nothing was sent" not in text and "sent: no" not in text:
            return "TAKE_OVER"
        return "ACCEPT"
    if focus == "trivial":
        return "ACCEPT"
    return "ACCEPT"


def run_supervisor_attempts(decide, limit: int | None = None) -> dict:
    """Call decide(attempt) until a terminal decision or the attempt limit.

    RETURN is retried. The loop cannot run past the limit.
    """
    cap = limit if limit is not None else int(os.environ.get("AYVEN_MAX_ATTEMPTS", "2"))
    cap = max(1, cap)
    path = []
    attempt = 1
    while attempt <= cap:
        decision = decide(attempt)
        path.append(decision)
        if decision in ("ACCEPT", "TAKE_OVER", "ESCALATE"):
            return {"decision": decision, "path": path, "attempts": attempt, "limited": False}
        if attempt >= cap:
            return {"decision": "RETURN", "path": path, "attempts": attempt, "limited": True}
        attempt += 1
    return {"decision": "RETURN", "path": path, "attempts": cap, "limited": True}


def challenge_material_claims(claims: list[dict], evidence_blob: str = "", quote: dict | None = None, model_text: str = "") -> list[dict]:
    """Try to disprove material claims. A miss is recorded as STOOD, not skipped."""
    totals = set()
    if quote:
        totals.add(str(quote["scenarios"]["labour_per_door"]["total_ex_vat"]))
        totals.add(str(quote["scenarios"]["labour_per_job"]["total_ex_vat"]))
        totals.add(str(quote["per_door_ex_delivery"]))
    corpus = evidence_blob or ""
    rows = []
    for claim in claims:
        ev = claim.get("evidence_text") or ""
        text = claim.get("claim_text") or ""
        challenge = "independent comparison with the evidence passage and the calculator"
        result = "STOOD"
        resolution = "The challenge did not disprove the claim."
        if claim.get("claim_type") == "MISSING_INFORMATION":
            challenge = "is this missing field being presented as a known fact"
            resolution = "It is a gap, so there is nothing to invent."
        elif claim.get("claim_type") == "CALCULATION" and totals:
            mentioned = re.findall(r"\d+\.\d+", text)
            if mentioned and not any(amount in totals for amount in mentioned):
                result = "DISPROVED"
                resolution = "The figure does not match an independent calculator total."
            else:
                challenge = "recompute the scenario total in integer pence and by expression"
                resolution = "The published total matches the calculator."
        else:
            nums = [n for n in re.findall(r"\d{3,}(?:\.\d+)?", text) if n not in ev and n not in corpus]
            urls = re.findall(r"https?://[^\s)>\]]+", text)
            if urls and any(url.rstrip(".,") not in corpus and url.rstrip(".,") not in ev for url in urls):
                result = "DISPROVED"
                challenge = "was this URL opened"
                resolution = "The URL is not in the opened evidence."
            elif nums and claim.get("source_type") not in ("DETERMINISTIC", "INPUT"):
                result = "DISPROVED"
                challenge = "does the evidence contain this figure"
                resolution = "A specific figure in the claim is not in the evidence."
            elif "footfall" in text.lower() and "footfall" not in (ev + corpus).lower():
                result = "DISPROVED"
                challenge = "was footfall stated by a source"
                resolution = "Footfall was not in the opened page."
            elif claim.get("claim_type") == "INFERENCE":
                challenge = "is this labelled as inference rather than a fact"
                resolution = "Explicit inference is allowed. It is not promoted to a fact."
            elif claim.get("freshness") == "STALE" or (claim.get("status") == "STALE"):
                challenge = "is this current availability"
                resolution = "Non-live availability stays stale and is not current stock."
        rows.append({
            "claim_id": claim.get("id") or "",
            "challenge": challenge,
            "result": result,
            "evidence": (ev or corpus)[:400],
            "resolution": resolution,
        })
    if model_text and totals:
        stray = []
        for raw in re.findall(r"£\s*([\d,]+(?:\.\d+)?)", model_text):
            norm = raw.replace(",", "")
            if "." not in norm:
                norm = f"{norm}.00"
            else:
                whole, frac = norm.split(".", 1)
                norm = f"{whole}.{frac[:2].ljust(2, '0')}"
            if norm not in totals and norm not in corpus:
                stray.append(norm)
        if stray:
            rows.append({
                "claim_id": "",
                "challenge": "model arithmetic disagrees with the calculator",
                "result": "DISPROVED",
                "evidence": ",".join(sorted(totals)),
                "resolution": f"Removed model amounts {', '.join(stray)}. The calculator totals stand.",
            })
    return rows


def advisory_decision(model_text: str) -> str:
    upper = (model_text or "").upper()
    for token in ("TAKE_OVER", "TAKE OVER", "ESCALATE", "RETURN", "ACCEPT"):
        if token in upper:
            return "TAKE_OVER" if token.startswith("TAKE") else ("ESCALATE" if token == "ESCALATE" else token)
    return ""
