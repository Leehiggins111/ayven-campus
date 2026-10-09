"""Configurable workforce model roles. Never hard-code a size forever."""
from __future__ import annotations

import os
import time

from . import llm

DEFAULT_MODELS = {
    "EMPLOYEE": (os.environ.get("AYVEN_EMPLOYEE_MODEL") or "Qwen/Qwen3-8B"),
    "SUPERVISOR": (os.environ.get("AYVEN_SUPERVISOR_MODEL") or "Qwen/Qwen3-32B"),
    "MANAGER": (os.environ.get("AYVEN_MANAGER_MODEL") or "Qwen/Qwen3-30B-A3B"),
    "ESCALATION": os.environ.get("AYVEN_ESCALATION_MODEL", ""),
}

_GENERATOR = None


def set_role_generator(fn):
    """Test or harness hook. The function returns (text, tokens, meta)."""
    global _GENERATOR
    _GENERATOR = fn


def role_model(role: str) -> str:
    return os.environ.get(f"AYVEN_{role.upper()}_MODEL") or DEFAULT_MODELS.get(role.upper(), DEFAULT_MODELS["EMPLOYEE"])


def local_base() -> str:
    return os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "")


def local_configured() -> bool:
    return bool(local_base())


def escalation_configured() -> bool:
    key = os.environ.get("AYVEN_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return bool(key) and os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"


def complete_role(role: str, system: str, user: str, max_tokens: int = 500, schema=None):
    from .intelligence.think import strip_think

    started = time.time()
    model = role_model(role)
    meta = {"role": role.upper(), "model": model, "backend": "stub", "elapsed_s": 0.0, "provider": "ayven"}
    if role.upper() == "ESCALATION" and not escalation_configured():
        text = "Frontier escalation is disabled. AYVEN_ALLOW_ESCALATION=0. No paid API call was made."
        meta["backend"] = "escalation_disabled"
        meta["elapsed_s"] = round(time.time() - started, 3)
        return text, 0, meta
    if _GENERATOR is not None and role.upper() != "ESCALATION":
        text, tokens, extra = _GENERATOR(role, system, user, max_tokens)
        meta.update(extra or {})
        meta["backend"] = (extra or {}).get("backend", meta.get("backend", "generator"))
        meta["elapsed_s"] = round(time.time() - started, 3)
        return strip_think(text), tokens, meta
    if local_base() and role.upper() != "ESCALATION":
        text, tokens, constrained = _openai_compat(local_base(), os.environ.get("AYVEN_LOCAL_LLM_API_KEY", "ayven-local"), model, system, user, max_tokens, schema=schema)
        meta["backend"] = "local_openai_compat"
        meta["constrained"] = constrained
        meta["model_validated"] = False
        meta["elapsed_s"] = round(time.time() - started, 3)
        return strip_think(text), tokens, meta
    if role.upper() == "ESCALATION" and escalation_configured():
        text, tokens = llm.complete(system, user, max_tokens=max_tokens)
        meta["backend"] = "frontier"
        meta["elapsed_s"] = round(time.time() - started, 3)
        return strip_think(text), tokens, meta
    if os.environ.get("AYVEN_LLM_STUB", "1") == "0":
        raise RuntimeError("No workforce endpoint configured; fixture fallback is disabled")
    text, tokens = llm.complete(f"[{role}/{model}] {system}", user, max_tokens=max_tokens)
    meta["elapsed_s"] = round(time.time() - started, 3)
    meta["backend"] = "stub"
    return strip_think(text), tokens, meta


def _openai_compat(base, key, model, system, user, max_tokens, schema=None):
    import httpx
    from .intelligence.boundary import separate_channels
    from .intelligence.constrained import enforce_output, server_body

    if os.environ.get("AYVEN_PROVIDER_FORMAT") == "ollama":
        return _ollama_chat(base, model, system, user, max_tokens, schema)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    body = server_body(model, messages, max_tokens, schema)
    if os.environ.get("AYVEN_PROVIDER_FORMAT", "qwen") in ("openai", "json_object"):
        # Standard hosted providers reject vLLM/llama.cpp extension fields.
        body.pop("guided_json", None)
        body.pop("grammar", None)
        if schema is not None and os.environ.get("AYVEN_PROVIDER_FORMAT") == "json_object":
            import json
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] += "\nReturn JSON matching this schema: " + json.dumps(schema.model_json_schema())
    r = httpx.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    message = data["choices"][0]["message"]
    text = message.get("content") or ""
    # A reasoning channel is not content. It is discarded before the caller sees the text.
    text = separate_channels(text, message.get("reasoning_content") or message.get("reasoning")).executable
    text, info = enforce_output(schema, text)
    tokens = int(data.get("usage", {}).get("total_tokens") or len(text) // 4)
    return text, tokens, info


def _ollama_chat(base, model, system, user, max_tokens, schema=None):
    """Native local API: explicit thinking control and model timings, no cloud fallback."""
    import httpx
    from urllib.parse import urlparse
    from .intelligence.boundary import separate_channels
    from .intelligence.constrained import enforce_output
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or "cloud" in model.lower():
        raise ValueError("Ollama workforce mode permits local models only")
    endpoint = f"{parsed.scheme}://{parsed.netloc}/api/chat"
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False, "think": False, "keep_alive": "15m",
            "options": {"num_predict": max_tokens, "temperature": 0.2}}
    if schema is not None:
        body["format"] = schema.model_json_schema()
    response = httpx.post(endpoint, json=body, timeout=120, follow_redirects=False)
    response.raise_for_status()
    data = response.json()
    if not data.get("done") or data.get("done_reason") == "length":
        raise RuntimeError("The local model did not finish its answer")
    message = data.get("message") or {}
    text = separate_channels(message.get("content") or "", message.get("thinking") or "").executable
    text, info = enforce_output(schema, text)
    info.update({"thinking_requested": False, "total_duration_s": round((data.get("total_duration") or 0) / 1e9, 3),
                 "load_duration_s": round((data.get("load_duration") or 0) / 1e9, 3), "output_tokens": data.get("eval_count") or 0})
    return text, int(data.get("prompt_eval_count") or 0) + int(data.get("eval_count") or 0), info
