from __future__ import annotations

import os
from typing import Any

import httpx

STUB = os.environ.get("AYVEN_LLM_STUB", "1") != "0"
BASE = os.environ.get("AYVEN_LLM_BASE_URL", "https://api.x.ai/v1")
MODEL = os.environ.get("AYVEN_LLM_MODEL", "grok-3")
API_KEY = os.environ.get("AYVEN_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


def complete(system: str, user: str, max_tokens: int = 600) -> tuple[str, int]:
    """Return (text, estimated_tokens). Never send chain-of-thought to callers."""
    if STUB or not API_KEY:
        text = stub_complete(user)
        return text, max(32, len(text) // 4)
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        r = httpx.post(f"{BASE.rstrip('/')}/chat/completions", json=payload, headers=headers, timeout=45)
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens = int(usage.get("total_tokens") or len(text) // 4)
        return text, tokens
    except Exception as exc:
        return stub_complete(user) + f"\n\n[provider fallback: {type(exc).__name__}]", 48


def stub_complete(user: str) -> str:
    u = user.lower()
    if "ticket" in u or "dortmund" in u or "ajax" in u:
        return (
            "Findings (stub, no live LLM key):\n"
            "- Clubs typically sell via official ticketing sites and authorised resellers, not bulk inventory to unknown operators.\n"
            "- Authorised sports travel organisers (ATOL/package-travel regulated in some markets) package official allocations.\n"
            "- Feasible model: agency / package-travel partnership, not speculative ticket inventory.\n"
            "- Next: contact authorised wholesalers; do not scrape or tout unofficial resale."
        )
    if "compliance" in u or "package" in u:
        return (
            "Compliance notes (stub):\n"
            "- Ticket touting / unauthorised resale is restricted in several EU jurisdictions.\n"
            "- Package Travel regulations may apply if transport+ticket+accommodation are bundled.\n"
            "- Do not send supplier emails until Lee approves outreach copy."
        )
    if "synthes" in u or "consolidat" in u or "milo" in u:
        return (
            "Milo synthesis (stub):\n"
            "A European football-trips offer around Dortmund, Ajax, Sparta Prague and Rosenborg can be explored "
            "without buying inventory if Ayven partners with authorised ticket/package wholesalers and stays off "
            "secondary tout markets. Economics are margin-on-package, not inventory speculation. "
            "Outreach to named authorised suppliers requires Lee approval."
        )
    return "Working notes recorded. Sources should be verified against official club and authorised-seller pages."
