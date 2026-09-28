"""Constrained generation for the Qwen runtimes the pod actually starts.

The validation harness employee is transformers (``HfSession.generate``).
Supervisor and manager in that harness are llama.cpp (``GgufSession``).
``scripts/gpu_employee.sh`` and ``scripts/gpu_manager.sh`` are vLLM OpenAI
servers. ``scripts/gpu_supervisor.sh`` is llama-server. The API client is
``app.models._openai_compat``.

Every structured call sends the same JSON schema:

* ``response_format`` (OpenAI json_schema, strict) for vLLM and llama.cpp
* ``guided_json`` for vLLM guided decoding
* an in-process logits mask that keeps the first new token as ``{`` for transformers
* ``response_format`` on ``llama_cpp.Llama.create_chat_completion``

``boundary.parse_model`` stays the backstop. Nothing here has been answered by
a GPU, so the probe status is MODEL_UNVALIDATED until that first live call.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


class ProbeAck(BaseModel):
    ok: bool = True


def server_body(model: str, messages: list[dict], max_tokens: int, schema: type[BaseModel] | None, temperature: float = 0.2) -> dict:
    """HTTP body for vLLM and llama-server. Extra keys are the structured-output contract."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if schema is None:
        return body
    from .boundary import decoding_kwargs

    decoded = decoding_kwargs(schema)
    body["response_format"] = decoded["response_format"]
    body["guided_json"] = decoded["response_format"]["json_schema"]["schema"]
    if decoded.get("llguidance_grammar"):
        body["grammar"] = decoded["llguidance_grammar"]
    return body


def llama_cpp_kwargs(schema: type[BaseModel] | None) -> dict:
    if schema is None:
        return {}
    from .boundary import decoding_kwargs

    decoded = decoding_kwargs(schema)
    return {"response_format": decoded["response_format"]}


def enforce_output(schema: type[BaseModel] | None, text: str) -> tuple[str, dict]:
    """Post-check. A rejected schema keeps the stripped text so salvage can run."""
    if schema is None:
        return text, {"postcheck": "skipped", "model_validated": False}
    from .boundary import parse_model

    parsed, attempts, error = parse_model(schema, text)
    info = {"postcheck": "accepted" if parsed is not None else "rejected", "attempts": attempts, "error": error, "model_validated": False}
    if parsed is None:
        return text, info
    return parsed.model_dump_json(), info


def brace_token_ids(encode) -> list[int]:
    """Token ids whose first piece is an opening brace. ``encode`` adds no specials."""
    found: list[int] = []
    for piece in ("{", " {", "{\n", "{\""):
        try:
            encoded = encode(piece)
        except Exception:
            continue
        if encoded is None:
            continue
        if isinstance(encoded, int):
            found.append(encoded)
            continue
        if len(encoded):
            found.append(int(encoded[0]))
    return sorted(set(found))


def mask_first_scores(scores: list[float], allowed: list[int]) -> list[float]:
    """CPU stand-in for the transformers logits processor: only ``{`` survives the first step."""
    if not allowed:
        return list(scores)
    masked = [float("-inf")] * len(scores)
    for index in allowed:
        if 0 <= index < len(scores):
            masked[index] = scores[index]
    return masked


def brace_processor(tokenizer):
    """Transformers logits processor. Import happens only when a model is loaded."""
    from transformers import LogitsProcessor

    allowed = brace_token_ids(lambda text: tokenizer.encode(text, add_special_tokens=False))

    class _OpeningBrace(LogitsProcessor):
        def __init__(self):
            self.applied = False
            self.used = False

        def __call__(self, input_ids, scores):
            if self.applied or not allowed:
                return scores
            self.applied = True
            self.used = True
            import torch

            masked = torch.full_like(scores, float("-inf"))
            for index in allowed:
                if index < scores.shape[-1]:
                    masked[..., index] = scores[..., index]
            return masked

    return _OpeningBrace()


def contract_report(base_url: str = "") -> dict:
    """Prove the request shape on CPU. A live server is contacted only when a URL is set."""
    from .boundary import first_token_is_schema

    body = server_body("probe", [{"role": "user", "content": "Reply with ok true as JSON."}], 16, ProbeAck)
    report = {
        "status": "MODEL_UNVALIDATED",
        "model_validated": False,
        "gpu_validated": False,
        "runtime": "vllm guided_json + llama.cpp response_format + transformers first-token mask",
        "request_keys": sorted(body),
        "has_response_format": "response_format" in body,
        "has_guided_json": isinstance(body.get("guided_json"), dict),
        "has_grammar": bool(body.get("grammar")),
        "first_token_is_brace": bool(first_token_is_schema(ProbeAck)),
        "postcheck": "boundary.parse_model",
    }
    if not base_url:
        report["reason"] = "No server URL. The contract is proven on CPU. A GPU runtime has not answered."
        return report
    import httpx

    try:
        response = httpx.post(f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=10)
        report["http_status"] = response.status_code
        report["server_answered"] = response.status_code < 500
        if response.status_code == 200:
            payload = response.json()
            message = (payload.get("choices") or [{}])[0].get("message") or {}
            text, info = enforce_output(ProbeAck, message.get("content") or "")
            report["postcheck_result"] = info
            report["sample"] = text[:200]
            accepted = info.get("postcheck") == "accepted"
            report["status"] = "SERVER_ACCEPTED_SCHEMA" if accepted else "SERVER_ANSWERED_POSTCHECK_REJECTED"
            report["model_validated"] = False
        else:
            report["status"] = "SERVER_REJECTED_REQUEST"
            report["reason"] = response.text[:300]
    except Exception as exc:
        report["status"] = "MODEL_UNVALIDATED"
        report["reason"] = f"{type(exc).__name__}: {exc}"
    return report


def write_probe(path: str, base_url: str = "") -> dict:
    import os
    from pathlib import Path

    url = base_url or os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "")
    report = contract_report(url)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
