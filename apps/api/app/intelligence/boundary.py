"""Separate intelligence from control.

Reasoning is discarded before any tool argument is read. Executable calls are
Pydantic objects. When llguidance is installed, a JSON-schema grammar rejects
any token that is not in that schema, including a reasoning prefix.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from .schemas import (
    BrowserRequest,
    CalculationRequest,
    ClaimProposal,
    FetchRequest,
    ManagerDecision,
    MemoryProposal,
    ResearchPlan,
    SearchRequest,
    SupervisorDecision,
)
from .think import contains_think

_NOISE_EXACT = {"think", "thinking", "reasoning", "redacted_thinking", "thought", "thoughts", "cot"}
_MEDIA = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".mp4", ".webm", ".css", ".js", ".woff", ".woff2", ".svg")
_NARRATION = (
    "let's tackle", "lets tackle", "the user wants", "the user need", "step by step",
    "i'll search", "i will search", "i need to", "the search query", "the search term",
    "first, the objective", "okay, let's", "okay, lets", "let me ",
)
_QUOTED = re.compile(r"[\"“]([^\"“”]{4,120})[\"”]")
_OPENERS = (
    ("<think", "</think>"),
    ("<|think|>", "<|/think|>"),
    ("<redacted_thinking", "</redacted_thinking>"),
    ("<reasoning", "</reasoning>"),
)
_TOOL_LINE = re.compile(r"^TOOL\s+([A-Za-z0-9_\-]+)\s+(\{.*\})\s*$")
_CALC = re.compile(r"^[0-9\.\+\-\*/\(\)\s]+$")

_TOKENIZER = None
_GRAMMARS: dict[str, str] = {}


@dataclass
class Channels:
    executable: str
    discarded: str
    reasoning_field: str = ""


@dataclass
class ToolGate:
    ok: bool
    call: dict = field(default_factory=dict)
    error: str = ""
    schema_name: str = ""


def separate_channels(text: str | None, reasoning_field: str | None = None) -> Channels:
    """Drop reasoning by scanning markers. The discarded channel is never returned as a tool input."""
    raw = text or ""
    discarded: list[str] = []
    if reasoning_field:
        discarded.append(reasoning_field)
    out: list[str] = []
    i = 0
    lower = raw.lower()
    while i < len(raw):
        opener = None
        closer = None
        at = None
        for start, end in _OPENERS:
            found = lower.find(start, i)
            if found != -1 and (at is None or found < at):
                at = found
                opener = start
                closer = end
        if opener is None or at is None or closer is None:
            out.append(raw[i:])
            break
        out.append(raw[i:at])
        close_at = lower.find(closer, at + len(opener))
        if close_at == -1:
            discarded.append(raw[at:])
            break
        discarded.append(raw[at : close_at + len(closer)])
        i = close_at + len(closer)
    executable = "".join(out).strip()
    return Channels(executable=executable, discarded="".join(discarded), reasoning_field=reasoning_field or "")


def reject_reasoning_query(query: str | None) -> str:
    """Return a reason when a string must not become a search, URL, or memory write."""
    raw = query or ""
    if contains_think(raw) or "<|" in raw or "</" in raw and "think" in raw.lower():
        return "reasoning_marker"
    cleaned = separate_channels(raw).executable.strip()
    if not cleaned:
        return "empty_after_channel_split"
    if cleaned != raw.strip() and contains_think(raw):
        return "reasoning_marker"
    lowered = cleaned.lower().strip().strip("\"'`")
    if lowered in _NOISE_EXACT or lowered in {f"think{ext}" for ext in _MEDIA}:
        return "reasoning_artifact"
    if "think.mp3" in lowered or lowered.endswith("/think") or "/think.mp3" in lowered:
        return "reasoning_artifact"
    tokens = re.findall(r"[a-z0-9]+", lowered)
    if len(tokens) == 1 and tokens[0] in _NOISE_EXACT:
        return "reasoning_token"
    if any(lowered.endswith(ext) for ext in _MEDIA) and not any(tok not in _NOISE_EXACT and tok not in {ext[1:] for ext in _MEDIA} for tok in tokens):
        return "media_noise"
    if _planning_prose(lowered):
        return "planning_prose"
    return ""


def _planning_prose(lowered: str) -> bool:
    """A chain-of-thought paragraph is not a search query."""
    if any(phrase in lowered for phrase in _NARRATION):
        return True
    sentences = [part for part in re.split(r"[.!?]+", lowered) if part.strip()]
    first_person = any(phrase in lowered for phrase in ("i'll", "i will", "the user", "let me", "i need", "i should", "the query would", "the search term", "the search query"))
    if len(sentences) >= 2 and first_person:
        return True
    if len(lowered) > 160 and first_person:
        return True
    return False


def quoted_queries(text: str) -> list[str]:
    """Keep a short quoted search buried in planning prose. The prose itself is not a query."""
    found = []
    for match in _QUOTED.findall(text or ""):
        item = match.strip()
        if item and not reject_reasoning_query(item) and item not in found:
            found.append(item)
    return found


def is_noise_hit(url: str = "", title: str = "", snippet: str = "") -> bool:
    blob = f"{url}\n{title}\n{snippet}".lower()
    if (url and reject_reasoning_query(url)) or (title and reject_reasoning_query(title)):
        return True
    if "think.mp3" in blob or "<think" in blob:
        return True
    path = urlparse(url or "").path.lower()
    if path.endswith(_MEDIA):
        return True
    for part in [piece for piece in path.split("/") if piece]:
        stem = re.split(r"[._(\-]", part, maxsplit=1)[0]
        if stem in _NOISE_EXACT or part in _NOISE_EXACT:
            return True
    return False


def _json_blob(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    for index, char in enumerate(text[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _grammar(schema: dict) -> str | None:
    try:
        from llguidance import grammar_from
    except Exception:
        return None
    key = json.dumps(schema, sort_keys=True)
    cached = _GRAMMARS.get(key)
    if cached:
        return cached
    compiled = grammar_from("json_schema", key)
    _GRAMMARS[key] = compiled
    return compiled


def _tokenizer():
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    try:
        from llguidance import LLTokenizer, TokenizerWrapper
    except Exception:
        return None

    class _Bytes:
        eos_token_id = 256
        bos_token_id = None
        tokens = [bytes([i]) for i in range(256)] + [b"<eos>"]
        special_token_ids = [256]

        def __call__(self, data):
            if isinstance(data, str):
                data = data.encode()
            return list(data)

    _TOKENIZER = LLTokenizer(TokenizerWrapper(_Bytes()), slices=[])
    return _TOKENIZER


def llguidance_status() -> dict:
    tok = _tokenizer()
    return {
        "repo": "guidance-ai/llguidance",
        "installed": tok is not None,
        "mode": "json_schema_grammar" if tok is not None else "unavailable",
        "during_decoding": tok is not None,
        "note": "A byte-level grammar mask allows only schema tokens. A reasoning prefix cannot be the first token of a tool request.",
    }


def grammar_allows(model: type[BaseModel], text: str) -> bool:
    """True only when every character is accepted by the schema grammar."""
    schema = model.model_json_schema()
    grammar = _grammar(schema)
    tok = _tokenizer()
    if grammar is None or tok is None:
        return False
    from llguidance import LLMatcher

    matcher = LLMatcher(tok, grammar, log_level=0)
    tokens = tok.tokenize_str(text or "")
    if not tokens:
        return False
    accepted = matcher.validate_tokens(tokens)
    return accepted == len(tokens) and not matcher.is_error()


def first_token_is_schema(model: type[BaseModel]) -> bool:
    """The grammar's first legal byte is '{', never '<'."""
    schema = model.model_json_schema()
    grammar = _grammar(schema)
    tok = _tokenizer()
    if grammar is None or tok is None:
        return False
    from llguidance import LLMatcher

    matcher = LLMatcher(tok, grammar, log_level=0)
    mask = matcher.compute_bitmask()
    allowed = [idx for idx in range(min(257, len(mask) * 8)) if mask[idx // 8] & (1 << (idx % 8))]
    return 123 in allowed and ord("<") not in allowed


def decoding_kwargs(model: type[BaseModel]) -> dict:
    """Arguments for an OpenAI-compatible server and a llama.cpp grammar string."""
    schema = model.model_json_schema()
    name = model.__name__
    grammar = _grammar(schema)
    return {
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
        "llguidance_grammar": grammar,
        "constrained": grammar is not None,
        "schema_name": name,
    }


def parse_model(model: type[BaseModel], text: str, *, attempts: int = 2) -> tuple[BaseModel | None, int, str]:
    """Validate structured output. Malformed JSON is repaired a capped number of times. Reasoning is ignored."""
    channels = separate_channels(text)
    candidate = _json_blob(channels.executable)
    last_error = ""
    for attempt in range(attempts + 1):
        if not candidate:
            last_error = "no_json_object"
            break
        if llguidance_status()["installed"] and not grammar_allows(model, candidate):
            last_error = "grammar_rejected"
        else:
            try:
                return model.model_validate_json(candidate), attempt, ""
            except ValidationError as exc:
                last_error = str(exc).splitlines()[0][:180]
            except ValueError as exc:
                last_error = str(exc)[:180]
        candidate = _repair_json(candidate)
    return None, attempts, last_error or "malformed"


def _repair_json(text: str) -> str:
    cleaned = text.replace("\n", " ").strip()
    cleaned = re.sub(r",\s*}", "}", cleaned)
    cleaned = re.sub(r",\s*]", "]", cleaned)
    return _json_blob(cleaned)


def extract_executable(text: str) -> tuple[list[dict], str]:
    """Tool lines are read only from the executable channel."""
    channels = separate_channels(text)
    calls: list[dict] = []
    prose: list[str] = []
    for line in channels.executable.splitlines():
        match = _TOOL_LINE.match(line.strip())
        if not match:
            prose.append(line)
            continue
        calls.append({"name": match.group(1), "arguments": match.group(2)})
    return calls, "\n".join(prose).strip()


def validate_tool_call(call: dict) -> ToolGate:
    """Runtime executes this result only when ok is true."""
    name = str(call.get("name") or "")
    raw_args = call.get("arguments")
    if not isinstance(raw_args, str):
        raw_args = json.dumps(raw_args) if raw_args is not None else ""
    channels = separate_channels(raw_args)
    if channels.discarded and not channels.executable:
        return ToolGate(False, error="arguments_were_reasoning", schema_name=name)
    payload_text = channels.executable
    try:
        parsed = json.loads(payload_text) if payload_text.startswith("{") else {"payload": payload_text}
    except json.JSONDecodeError:
        parsed = {"payload": payload_text}
    if not isinstance(parsed, dict):
        return ToolGate(False, error="arguments_not_object", schema_name=name)
    tool = str(parsed.get("tool") or (name if name != "ayven_tool" else ""))
    payload = parsed.get("payload", parsed)
    if isinstance(payload, str):
        reason = reject_reasoning_query(payload) if tool in {"web_search", "fetch_page", "browser", "memory"} else ""
        if reason and tool in {"web_search", "fetch_page", "browser"}:
            return ToolGate(False, error=reason, schema_name=tool or name)
        if tool == "calculator" and not _CALC.match(payload.strip()):
            return ToolGate(False, error="calculator_not_arithmetic", schema_name="CalculationRequest")
        if tool == "web_search":
            try:
                req = SearchRequest(query=payload.strip(), reason="tool")
            except ValidationError as exc:
                return ToolGate(False, error=str(exc).splitlines()[0][:160], schema_name="SearchRequest")
            if not req.query:
                return ToolGate(False, error="empty_query", schema_name="SearchRequest")
        if tool in {"fetch_page", "browser"}:
            url = payload.strip()
            if not _http_url(url):
                return ToolGate(False, error="invalid_url", schema_name="FetchRequest" if tool == "fetch_page" else "BrowserRequest")
        cleaned = json.dumps({"tool": tool or name, "payload": payload if isinstance(payload, str) else json.dumps(payload)})
        return ToolGate(True, {"name": "ayven_tool" if name == "ayven_tool" or tool else name, "arguments": cleaned, "tool": tool, "payload": payload if isinstance(payload, str) else json.dumps(payload)}, schema_name=tool or name)
    schema = _schema_for(tool, payload)
    if schema is None:
        if reject_reasoning_query(json.dumps(payload)):
            return ToolGate(False, error="reasoning_artifact", schema_name=tool or name)
        return ToolGate(True, {"name": name, "arguments": json.dumps(payload), "tool": tool or name, "payload": json.dumps(payload)}, schema_name=tool or name)
    if llguidance_status()["installed"]:
        blob = json.dumps(payload)
        if not grammar_allows(schema, blob):
            return ToolGate(False, error="grammar_rejected", schema_name=schema.__name__)
    try:
        obj = schema.model_validate(payload)
    except ValidationError as exc:
        return ToolGate(False, error=str(exc).splitlines()[0][:160], schema_name=schema.__name__)
    return ToolGate(True, {"name": name, "arguments": obj.model_dump_json(), "tool": tool or name, "payload": obj.model_dump_json()}, schema_name=schema.__name__)


def _schema_for(tool: str, payload: dict) -> type[BaseModel] | None:
    if "query" in payload and tool in {"", "web_search", "search"}:
        return SearchRequest
    if "url" in payload and tool in {"fetch_page", "http_fetch"}:
        return FetchRequest
    if "url" in payload and tool == "browser":
        return BrowserRequest
    if "expression" in payload:
        return CalculationRequest
    if "claim" in payload:
        return ClaimProposal
    if "content" in payload and "scope" in payload:
        return MemoryProposal
    if "decision" in payload and "rationale" in payload and tool == "supervisor":
        return SupervisorDecision
    if "decision" in payload and tool == "manager":
        return ManagerDecision
    if "queries" in payload:
        return ResearchPlan
    return None


def _http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and " " not in url


def parse_decision_json(model: type[BaseModel], text: str) -> Any:
    obj, _attempts, _error = parse_model(model, text)
    return obj


def queries_from_plan_text(text: str) -> list[str]:
    """Pull queries from a validated plan. Lines inside reasoning are not queries."""
    plan, _attempts, _error = parse_model(ResearchPlan, text)
    if plan is not None:
        found = [item.strip() for item in plan.queries if item and item.strip()]
    else:
        found = []
        for line in separate_channels(text).executable.splitlines():
            item = line.strip().lstrip("-*0123456789.) ").strip()
            if item:
                found.append(item)
    kept = []
    for query in found:
        if reject_reasoning_query(query):
            for salvaged in quoted_queries(query):
                if salvaged not in kept:
                    kept.append(salvaged[:180])
            continue
        short = query[:180]
        if short not in kept:
            kept.append(short)
    return kept[:8]
