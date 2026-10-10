"""Configurable workforce model roles. Never hard-code a size forever."""
from __future__ import annotations

import os
import re
import time

from . import llm

DEFAULT_MODELS = {
    "EMPLOYEE": os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B"),
    "SUPERVISOR": os.environ.get("AYVEN_SUPERVISOR_MODEL", "Qwen/Qwen3-32B"),
    "MANAGER": os.environ.get("AYVEN_MANAGER_MODEL", "Qwen/Qwen3-30B-A3B"),
    "ESCALATION": os.environ.get("AYVEN_ESCALATION_MODEL", ""),
}

_GENERATOR = None


def set_role_generator(fn):
    """Test or harness hook. The function returns (text, tokens, meta)."""
    global _GENERATOR
    _GENERATOR = fn


def role_model(role: str) -> str:
    return DEFAULT_MODELS.get(role.upper(), DEFAULT_MODELS["EMPLOYEE"])


def local_base() -> str:
    return os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "")


def local_configured() -> bool:
    return bool(local_base())


def escalation_configured() -> bool:
    key = os.environ.get("AYVEN_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return bool(key) and os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"


def complete_role(role: str, system: str, user: str, max_tokens: int = 500, schema=None, prefill: str = ""):
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
        text, tokens, constrained = _openai_compat(local_base(), os.environ.get("AYVEN_LOCAL_LLM_API_KEY", "ayven-local"), model, system, user, max_tokens, schema=schema, prefill=prefill)
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
    text, tokens = llm.complete(f"[{role}/{model}] {system}", user, max_tokens=max_tokens)
    meta["elapsed_s"] = round(time.time() - started, 3)
    meta["backend"] = "stub"
    return strip_think(text), tokens, meta


def ollama_thinking_enabled() -> bool:
    return os.environ.get("AYVEN_OLLAMA_THINK", "0") == "1"


def local_request_body(base: str, body: dict) -> dict:
    """Ollama's OpenAI route ignores a `think` field. `reasoning_effort: none` is the switch it reads."""
    if ":11434" not in (base or ""):
        return body
    plain = {
        "model": body.get("model"),
        "messages": body.get("messages") or [],
        "max_tokens": body.get("max_tokens") or 400,
        "temperature": body.get("temperature", 0.2),
        "think": ollama_thinking_enabled(),
        "reasoning_effort": "none" if not ollama_thinking_enabled() else "medium",
    }
    return plain


def ollama_raw_prompt(system: str, user: str, prefill: str = "") -> str:
    """ChatML that already closed the think block. qwen3 then writes the answer, not the reasoning."""
    return (
        "<|im_start|>system\n"
        + (system or "").strip()
        + "\n<|im_end|>\n"
        + "<|im_start|>user\n"
        + (user or "").strip()
        + " /no_think<|im_end|>\n"
        + "<|im_start|>assistant\n"
        + "<think>\n\n</think>\n\n"
        + (prefill or "")
    )


def ollama_native_body(model: str, system: str, user: str, max_tokens: int, prefill: str = "") -> dict:
    """Native /api/generate with raw ChatML. The OpenAI route cannot turn qwen3 thinking off."""
    predict = max(int(max_tokens or 0), 64)
    return {
        "model": model,
        "prompt": ollama_raw_prompt(system, user, prefill),
        "raw": True,
        "stream": False,
        "think": ollama_thinking_enabled(),
        "options": {
            "num_predict": predict,
            "num_ctx": max(8192, predict + 4096),
            "temperature": 0.2,
            "stop": ["<|im_end|>", "<|im_start|>"],
        },
    }


def ollama_answer(payload: dict) -> str:
    """Visible answer only. Text inside a think block, and the thinking field, are discarded."""
    from .intelligence.think import strip_think

    message = payload.get("message") or {}
    if not message and payload.get("choices"):
        message = (payload["choices"][0] or {}).get("message") or {}
    content = message.get("content") or payload.get("response") or ""
    marker = "</think>"
    lowered = content.lower()
    at = lowered.rfind(marker)
    if at != -1:
        content = content[at + len(marker) :]
    content = re.sub(r"/no_think", "", content, flags=re.I)
    return strip_think(content).strip()


def _openai_compat(base, key, model, system, user, max_tokens, schema=None, prefill: str = ""):
    import httpx
    from .intelligence.constrained import enforce_output, server_body

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    timeout = float(os.environ.get("AYVEN_LLM_TIMEOUT_S", "600" if ":11434" in (base or "") else "90"))
    if ":11434" in (base or ""):
        text, tokens = _ollama_native(base, model, messages, max_tokens, timeout, prefill=prefill)
        text, info = enforce_output(schema, text)
        if not (text or "").strip():
            raise RuntimeError("empty model content")
        return text, tokens, info
    body = local_request_body(base, server_body(model, messages, max_tokens, schema))
    r = httpx.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=timeout,
    )
    if r.status_code == 400 and any(key_name in body for key_name in ("guided_json", "grammar", "response_format")):
        r = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=local_request_body(base, {k: body[k] for k in ("model", "messages", "max_tokens", "temperature") if k in body}),
            timeout=timeout,
        )
    r.raise_for_status()
    data = r.json()
    text = ollama_answer(data)
    text, info = enforce_output(schema, text)
    if not (text or "").strip():
        raise RuntimeError("empty model content")
    tokens = int(data.get("usage", {}).get("total_tokens") or len(text) // 4)
    return text, tokens, info


def _ollama_root(base: str) -> str:
    root = (base or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root


def _ollama_native(base: str, model: str, messages: list[dict], max_tokens: int, timeout: float, prefill: str = "") -> tuple[str, int]:
    """Ask twice at most. A reasoning field is never copied into the answer."""
    import httpx

    system = ""
    user = ""
    for message in messages:
        if message.get("role") == "system":
            system = message.get("content") or ""
        elif message.get("role") == "user":
            user = message.get("content") or ""
    url = f"{_ollama_root(base)}/api/generate"
    first = max(int(max_tokens or 0), 64)
    # The second call runs only when the first answer is empty. It is larger so a
    # think block that consumed the first budget can finish and still leave an answer.
    second = min(4096, max(first * 2, 1024))
    budgets = (first, second)
    last_tokens = 0
    for predict in budgets:
        body = ollama_native_body(model, system, user, predict, prefill=prefill)
        response = httpx.post(url, json=body, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        last_tokens = int(data.get("eval_count") or 0)
        text = ollama_answer(data)
        if prefill:
            text = (prefill + text).strip()
        if text:
            return text, last_tokens or max(1, len(text) // 4)
    raise RuntimeError("empty model content")
