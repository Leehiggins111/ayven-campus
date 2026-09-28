"""Agent runtimes. Ayven owns the package. The runtime only proposes typed calls."""

from __future__ import annotations

import os
from typing import Any, Callable


class AgentRuntime:
    name = "base"

    def status(self) -> dict:
        return {"name": self.name, "active": False}

    def employee_turn(self, **kwargs) -> dict:
        raise NotImplementedError


class NativeRuntime(AgentRuntime):
    name = "native"

    def status(self) -> dict:
        return {"name": self.name, "active": True, "boundary": "typed tool gate"}

    def employee_turn(self, **kwargs) -> dict:
        from .qwen_adapter import strip_tool_lines
        from .think import strip_think

        return {"text": strip_tool_lines(strip_think(kwargs.get("preset_text") or "")), "tools": [], "runtime": "native"}


class QwenAgentRuntime(AgentRuntime):
    name = "qwen-agent"

    def status(self) -> dict:
        from . import qwen_adapter

        return qwen_adapter.status()

    def employee_turn(self, **kwargs) -> dict:
        from .qwen_adapter import employee_turn

        return employee_turn(**kwargs)


class PydanticRuntime(AgentRuntime):
    """Structured-output runtime using Pydantic schemas. It is not a Pydantic AI application."""

    name = "pydantic"

    def status(self) -> dict:
        return {
            "name": self.name,
            "active": True,
            "pydantic_ai": "not_installed",
            "uses": "pydantic models for tool args, decisions, and repair instructions",
        }

    def employee_turn(self, **kwargs) -> dict:
        from .boundary import extract_executable, validate_tool_call
        from .think import strip_think

        calls, prose = extract_executable(kwargs.get("preset_text") or "")
        accepted = []
        for call in calls:
            gate = validate_tool_call(call)
            if gate.ok:
                accepted.append(gate.call)
        return {"text": strip_think(prose), "tools": accepted, "runtime": "pydantic"}


def select_runtime(name: str | None = None) -> AgentRuntime:
    raw = (name or os.environ.get("AYVEN_AGENT_RUNTIME", "auto")).strip().lower()
    if raw in {"pydantic", "pydantic-ai"}:
        return PydanticRuntime()
    if raw == "native":
        return NativeRuntime()
    if raw in {"qwen-agent", "qwen_agent", "qwen", "auto"}:
        from .qwen_adapter import available

        if raw != "native" and available() and raw != "pydantic":
            if raw == "auto" or raw.startswith("qwen"):
                return QwenAgentRuntime()
        if raw == "auto":
            return NativeRuntime()
    return NativeRuntime()


def run_employee(handler: Callable[..., Any] | None = None, **kwargs) -> dict:
    runtime = select_runtime()
    if handler is not None:
        kwargs["handler"] = handler
    return runtime.employee_turn(**kwargs)
