from __future__ import annotations

import os
from typing import Any

import httpx

BASE = os.environ.get("AYVEN_LLM_BASE_URL", "https://api.x.ai/v1")
MODEL = os.environ.get("AYVEN_LLM_MODEL", "grok-3")


def _api_key() -> str:
    return os.environ.get("AYVEN_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def _stub_enabled() -> bool:
    return os.environ.get("AYVEN_LLM_STUB", "1") != "0"


def _escalation_allowed() -> bool:
    return os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"


def complete(system: str, user: str, max_tokens: int = 600) -> tuple[str, int]:
    """Return (text, estimated_tokens). Paid calls require AYVEN_ALLOW_ESCALATION=1."""
    from .intelligence.think import strip_think

    if _stub_enabled() or not _api_key() or not _escalation_allowed():
        text = stub_complete(user)
        return strip_think(text), max(32, len(text) // 4)
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {_api_key()}"}
    try:
        r = httpx.post(f"{BASE.rstrip('/')}/chat/completions", json=payload, headers=headers, timeout=45)
        r.raise_for_status()
        data = r.json()
        text = strip_think(data["choices"][0]["message"]["content"])
        usage = data.get("usage", {})
        tokens = int(usage.get("total_tokens") or len(text) // 4)
        return text, tokens
    except Exception as exc:
        return strip_think(stub_complete(user) + f"\n\n[provider fallback: {type(exc).__name__}]"), 48


def stub_complete(user: str) -> str:
    u = user.lower()
    if "research review" in u:
        return "SUFFICIENT\nThe opened pages cover the planned queries."
    if "manager judgement" in u:
        return "CLARIFY\nRationale: the evidence can support the briefing, and a person must approve any external action."
    prefix = ""
    if "ayven tool runtime" in u:
        prefix = 'TOOL ayven_tool {"tool":"record_review","payload":"reviewed the ledger"}\n'
    if "evidence:" in u:
        ev = user.split("Evidence:", 1)[-1].strip()[:1800]
        needs = any(k in u for k in ("price", "trade", "scotland", "hinge", "moq", "account"))
        enquiry = (
            "\n\nEnquiry draft (do not send until Lee approves):\n"
            "Hello — we are a joinery workshop in Scotland seeking trade supply of internal doors, "
            "including non-standard hinge positions. Please confirm trade pricing, MOQ, lead times and delivery to Scotland."
            if needs else ""
        )
        body = (
            "Findings from captured sources (stub synthesis; live model not configured):\n"
            f"{ev[:1400]}\n\n"
            "Gaps: public pages often omit trade price, MOQ and custom options. Confirm with the manufacturer."
            f"{enquiry}"
        )
    elif "ticket" in u or "dortmund" in u or "ajax" in u:
        body = (
            "Findings (stub, no live LLM key):\n"
            "- Clubs typically sell via official ticketing sites and authorised resellers.\n"
            "- Authorised sports travel organisers package official allocations.\n"
            "- Feasible model: agency / package-travel partnership, not inventory speculation.\n"
            "- Next: contact authorised wholesalers; do not scrape unofficial resale."
        )
    elif "synthes" in u or "consolidat" in u or "milo" in u:
        body = "Milo: Research Lab findings are ready for Lee."
    else:
        body = "Working notes recorded. Verify claims against primary manufacturer or official pages."
    return prefix + body
