"""Qwen-Agent function-calling runtime inside an Ayven employee boundary.

Ayven still owns the work package, permissions, evidence, and claims.
This module only runs the tool loop. A failure falls back to the native runtime.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from typing import Any, Callable

from .permissions import authorize
from .think import strip_think

TOOL_LINE = re.compile(r"^TOOL\s+([A-Za-z0-9_\-]+)\s+(\{.*\})\s*$")
_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "Ayven tool name"},
        "payload": {"type": "string", "description": "Tool argument"},
    },
    "required": ["tool"],
}


def available() -> bool:
    return importlib.util.find_spec("qwen_agent") is not None


def runtime_mode() -> str:
    """qwen-agent, native, or auto. Auto picks qwen-agent when the package imports."""
    raw = os.environ.get("AYVEN_AGENT_RUNTIME", "auto").strip().lower()
    if raw in ("qwen-agent", "qwen_agent", "qwen"):
        return "qwen-agent" if available() else "native"
    if raw == "native":
        return "native"
    return "qwen-agent" if available() else "native"


def enabled() -> bool:
    return runtime_mode() == "qwen-agent"


def status() -> dict:
    mode = runtime_mode()
    return {
        "repo": "QwenLM/Qwen-Agent",
        "version": _version(),
        "licence": "Apache-2.0",
        "installed": available(),
        "runtime": mode,
        "active": mode == "qwen-agent",
        "used_for_inference": mode == "qwen-agent",
        "boundary": "FnCallAgent calls Ayven tools only after authorize()",
        "note": "Ayven keeps packages, permissions, evidence, and approvals. Qwen-Agent does not.",
    }


def _version() -> str:
    try:
        import importlib.metadata as meta

        return meta.version("qwen-agent")
    except Exception:
        return "unknown"


def parse_tool_lines(text: str) -> tuple[list[dict], str]:
    calls: list[dict] = []
    prose: list[str] = []
    for line in (text or "").splitlines():
        match = TOOL_LINE.match(line.strip())
        if not match:
            prose.append(line)
            continue
        calls.append({"name": match.group(1), "arguments": match.group(2)})
    return calls, strip_think("\n".join(prose).strip())


def strip_tool_lines(text: str) -> str:
    return parse_tool_lines(text)[1]


def run_tool_loop(
    *,
    system: str,
    user: str,
    agent_id: str,
    package_id: str,
    preset_text: str = "",
    session: Any = None,
    handler: Callable[[str, str], str] | None = None,
    approved: bool = False,
    scripted_calls: list[dict] | None = None,
) -> dict:
    """Run one employee turn through FnCallAgent. Raises if the runtime cannot start."""
    if not available():
        raise RuntimeError("qwen_agent is not installed")
    from qwen_agent.agents.fncall_agent import FnCallAgent
    from qwen_agent.llm.base import BaseChatModel
    from qwen_agent.llm.schema import ASSISTANT, FUNCTION, FunctionCall, Message
    from qwen_agent.tools.base import BaseTool

    invoked: list[dict] = []

    class AyvenTool(BaseTool):
        name = "ayven_tool"
        description = "Call one approved Ayven tool. Ayven checks permission before any effect."
        parameters = _SCHEMA

        def call(self, params, **kwargs):
            args = self._verify_json_format_args(params)
            tool_name = str(args.get("tool") or "")
            payload = str(args.get("payload") or "")
            try:
                authorize(agent_id, tool_name, approved=approved)
            except Exception as exc:
                invoked.append({"tool": tool_name, "status": "denied", "error": str(exc)})
                return f"denied: {exc}"
            if handler is not None:
                output = handler(tool_name, payload)
            else:
                output = _default_handler(agent_id, package_id, tool_name, payload, approved=approved)
            invoked.append({"tool": tool_name, "status": "ok", "payload": payload[:180]})
            try:
                from .toolkit import ToolResult, now, record_tool_call

                record_tool_call(package_id, agent_id, ToolResult(tool=tool_name, status="ok", query=payload[:200], extracted_content=str(output)[:500], timestamp=now(), metadata={"runtime": "qwen-agent"}))
            except Exception:
                pass
            return output

    calls = list(scripted_calls or [])
    prose = strip_think(preset_text or "")
    if not calls and preset_text:
        calls, prose = parse_tool_lines(preset_text)
    llm = _chat_model(
        BaseChatModel,
        Message,
        FunctionCall,
        ASSISTANT,
        FUNCTION,
        calls,
        prose,
        session,
        system,
        user,
    )
    agent = FnCallAgent(function_list=[AyvenTool()], llm=llm, system_message=system or "Ayven tool runtime")
    messages = agent.run_nonstream([Message(role="user", content=user or "Continue.")])
    final = ""
    for message in messages:
        content = message.content if hasattr(message, "content") else message.get("content")
        role = message.role if hasattr(message, "role") else message.get("role")
        if role == "assistant" and isinstance(content, str) and content.strip():
            final = content.strip()
    if not final:
        final = prose
    return {
        "text": strip_tool_lines(strip_think(final)),
        "tools": invoked,
        "system_seen": system,
        "runtime": "qwen-agent",
    }


def _chat_model(BaseChatModel, Message, FunctionCall, ASSISTANT, FUNCTION, calls, prose, session, system, user):
    live = session is not None
    local = os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "")
    if live or (local and not calls and not prose):
        return _LiveChat(BaseChatModel, Message, FunctionCall, ASSISTANT, FUNCTION, session, system)

    class Scripted(BaseChatModel):
        def __init__(self):
            super().__init__({"model": "ayven-scripted", "model_type": "ayven"})
            self.pending = list(calls)

        def _emit(self, message, stream):
            if stream:
                def generate():
                    yield [message]
                return generate()
            return [message]

        def _chat_with_functions(self, messages, functions, stream, delta_stream, generate_cfg, lang):
            last = messages[-1] if messages else None
            role = getattr(last, "role", None)
            if role == FUNCTION or not self.pending:
                return self._emit(Message(role=ASSISTANT, content=prose or "Working notes recorded."), stream)
            call = self.pending.pop(0)
            name = call.get("name") or "ayven_tool"
            arguments = call.get("arguments") or "{}"
            if name != "ayven_tool":
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed = {"payload": arguments}
                if "tool" not in parsed:
                    parsed = {"tool": name, "payload": arguments}
                arguments = json.dumps(parsed)
                name = "ayven_tool"
            return self._emit(
                Message(role=ASSISTANT, content="", function_call=FunctionCall(name=name, arguments=arguments), extra={}),
                stream,
            )

        def _chat_stream(self, messages, delta_stream, generate_cfg):
            yield [Message(role=ASSISTANT, content=prose or "Working notes recorded.")]

        def _chat_no_stream(self, messages, generate_cfg):
            return [Message(role=ASSISTANT, content=prose or "Working notes recorded.")]

    return Scripted()


class _LiveChat:
    """BaseChatModel stand-in built without subclassing until qwen_agent is imported."""

    def __new__(cls, BaseChatModel, Message, FunctionCall, ASSISTANT, FUNCTION, session, system):
        class Live(BaseChatModel):
            def __init__(self):
                model = os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B")
                super().__init__({"model": model, "model_type": "ayven-live"})
                self.session = session
                self.system = system

            def _ask(self, messages) -> str:
                lines = []
                for message in messages:
                    content = message.content if isinstance(message.content, str) else ""
                    lines.append(f"{message.role}: {content}")
                prompt = "\n".join(lines)[-6000:]
                if self.session is not None:
                    text, _meta = self.session.generate(self.system, prompt)
                    return text or ""
                from qwen_agent.llm.oai import TextChatAtOAI

                remote = TextChatAtOAI({
                    "model": os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B"),
                    "model_type": "oai",
                    "model_server": os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", ""),
                    "api_key": os.environ.get("AYVEN_LOCAL_LLM_API_KEY", "EMPTY"),
                })
                reply = remote.chat(messages=messages, stream=False)
                if reply and isinstance(reply[0].content, str):
                    return reply[0].content
                return ""

            def _emit(self, message, stream):
                if stream:
                    def generate():
                        yield [message]
                    return generate()
                return [message]

            def _chat_with_functions(self, messages, functions, stream, delta_stream, generate_cfg, lang):
                if messages and messages[-1].role == FUNCTION:
                    text = self._ask(messages)
                    calls, prose = parse_tool_lines(text)
                    return self._emit(Message(role=ASSISTANT, content=prose or text or "Working notes recorded."), stream)
                text = self._ask(messages)
                calls, prose = parse_tool_lines(text)
                if calls:
                    call = calls[0]
                    return self._emit(
                        Message(
                            role=ASSISTANT,
                            content="",
                            function_call=FunctionCall(name="ayven_tool", arguments=call["arguments"] if call["name"] == "ayven_tool" else json.dumps({"tool": call["name"], "payload": call["arguments"]})),
                            extra={},
                        ),
                        stream,
                    )
                return self._emit(Message(role=ASSISTANT, content=prose or text), stream)

            def _chat_stream(self, messages, delta_stream, generate_cfg):
                yield [Message(role=ASSISTANT, content=self._ask(messages))]

            def _chat_no_stream(self, messages, generate_cfg):
                return [Message(role=ASSISTANT, content=self._ask(messages))]

        return Live()


def _default_handler(agent_id: str, package_id: str, tool_name: str, payload: str, approved: bool = False) -> str:
    if tool_name == "record_review":
        return "review noted; no evidence added"
    if tool_name == "calculator":
        from .calc import eval_arithmetic

        return eval_arithmetic(payload)
    if tool_name == "web_search":
        from ..tools import web_search

        hits = web_search(payload, limit=3)
        return json.dumps(hits)[:1500]
    if tool_name == "code_exec":
        from .code_sandbox import run_code

        result = run_code(agent_id, payload, approved=approved)
        return result.extracted_content or result.error or result.status
    return f"no handler for {tool_name}"


def employee_turn(
    *,
    system: str,
    user: str,
    agent_id: str,
    package_id: str,
    preset_text: str | None = None,
    session: Any = None,
    handler: Callable[[str, str], str] | None = None,
    approved: bool = False,
    scripted_calls: list[dict] | None = None,
) -> dict:
    """Best-effort employee turn. Falls back to native text when Qwen-Agent fails."""
    meta: dict = {}
    text = preset_text
    if text is None and session is None and not scripted_calls:
        from ..models import complete_role

        text, tokens, meta = complete_role("EMPLOYEE", system, user, max_tokens=320)
        meta = dict(meta)
        meta["completion_tokens"] = tokens
    if runtime_mode() != "qwen-agent":
        return {"text": strip_tool_lines(text or ""), "tools": [], "runtime": "native", "system_seen": system, "fallback": "runtime native", "meta": meta}
    try:
        result = run_tool_loop(
            system=system,
            user=user,
            agent_id=agent_id,
            package_id=package_id,
            preset_text=text or "",
            session=session,
            handler=handler,
            approved=approved,
            scripted_calls=scripted_calls,
        )
        result["meta"] = meta
        return result
    except Exception as exc:
        return {
            "text": strip_tool_lines(text or ""),
            "tools": [],
            "runtime": "native",
            "system_seen": system,
            "fallback": f"{type(exc).__name__}: {exc}",
            "meta": meta,
        }
