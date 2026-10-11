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
    """Optional pydantic-ai Agent. Ayven still owns the tool gate."""

    name = "pydantic"

    def status(self) -> dict:
        try:
            import pydantic_ai

            version = getattr(pydantic_ai, "__version__", "installed")
            present = True
        except Exception as exc:
            version = f"{type(exc).__name__}: {exc}"
            present = False
        return {
            "name": self.name,
            "active": present,
            "pydantic_ai": version if present else "not_installed",
            "owns_packages": False,
            "uses": "FunctionModel or TestModel, then Ayven validate_tool_call",
        }

    def employee_turn(self, **kwargs) -> dict:
        import os

        os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
        from .boundary import extract_executable, validate_tool_call
        from .think import strip_think

        raw = _pydantic_output(kwargs.get("preset_text") or "", kwargs.get("objective") or "Work the package.")
        calls, prose = extract_executable(raw)
        accepted = []
        rejected = []
        for call in calls:
            gate = validate_tool_call(call)
            if gate.ok:
                accepted.append(gate.call)
            else:
                rejected.append(gate.reason or "rejected")
        return {
            "text": strip_think(prose),
            "tools": accepted,
            "rejected": rejected,
            "runtime": "pydantic-ai",
            "model": "FunctionModel",
        }


def _pydantic_output(preset: str, objective: str) -> str:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models.test import TestModel

    def respond(_messages, _info):
        body = preset or "success (no tool calls)"
        return ModelResponse(parts=[TextPart(content=body)])

    agent = Agent(FunctionModel(respond))
    result = agent.run_sync(objective)
    # TestModel is executed so the optional runtime is proven, then discarded.
    Agent(TestModel()).run_sync("ping")
    return str(result.output)


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
