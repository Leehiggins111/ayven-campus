"""Configurable workforce model roles. Never hard-code a size forever."""
from __future__ import annotations

import os
import time

from . import llm

DEFAULT_MODELS = {
    "EMPLOYEE": os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B"),
    "SUPERVISOR": os.environ.get("AYVEN_SUPERVISOR_MODEL", "Qwen/Qwen3-32B"),
    "MANAGER": os.environ.get("AYVEN_MANAGER_MODEL", "Qwen/Qwen3-30B-A3B"),
    "ESCALATION": os.environ.get("AYVEN_ESCALATION_MODEL", ""),
}

LOCAL_BASE = os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "")
LOCAL_KEY = os.environ.get("AYVEN_LOCAL_LLM_API_KEY", "ayven-local")


def role_model(role: str) -> str:
    return DEFAULT_MODELS.get(role.upper(), DEFAULT_MODELS["EMPLOYEE"])


def local_configured() -> bool:
    return bool(LOCAL_BASE)


def escalation_configured() -> bool:
    return bool(os.environ.get("AYVEN_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")) and os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"


def complete_role(role: str, system: str, user: str, max_tokens: int = 500):
    started = time.time()
    model = role_model(role)
    meta = {"role": role.upper(), "model": model, "backend": "stub", "elapsed_s": 0.0}
    if LOCAL_BASE and role.upper() != "ESCALATION":
        text, tokens = _openai_compat(LOCAL_BASE, LOCAL_KEY, model, system, user, max_tokens)
        meta["backend"] = "local_openai_compat"
        meta["elapsed_s"] = round(time.time() - started, 3)
        return text, tokens, meta
    if role.upper() == "ESCALATION" and escalation_configured():
        text, tokens = llm.complete(system, user, max_tokens=max_tokens)
        meta["backend"] = "frontier"
        meta["elapsed_s"] = round(time.time() - started, 3)
        return text, tokens, meta
    text, tokens = llm.complete(f"[{role}/{model}] {system}", user, max_tokens=max_tokens)
    meta["elapsed_s"] = round(time.time() - started, 3)
    return text, tokens, meta


def _openai_compat(base, key, model, system, user, max_tokens):
    import httpx
    r = httpx.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0.2},
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    tokens = int(data.get("usage", {}).get("total_tokens") or len(text) // 4)
    return text, tokens
