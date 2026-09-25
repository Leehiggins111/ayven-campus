"""Model registry and task routing.

Benchmark defaults remain Qwen3-8B / 32B / 30B-A3B. They are not the only
route. Frontier escalation stays off unless AYVEN_ALLOW_ESCALATION=1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    provider: str
    local: bool
    context: int
    reasoning: float
    tools: float
    coding: float
    cost: float
    speed: float
    memory_gb: float
    quantisation: str
    roles: tuple[str, ...]
    tasks: tuple[str, ...]
    enabled: bool = True


def _escalation_enabled() -> bool:
    allow = os.environ.get("AYVEN_ALLOW_ESCALATION", "0") == "1"
    key = os.environ.get("AYVEN_LLM_API_KEY") or os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return bool(allow and key)


def registry() -> list[ModelSpec]:
    employee = os.environ.get("AYVEN_EMPLOYEE_MODEL", "Qwen/Qwen3-8B")
    supervisor = os.environ.get("AYVEN_SUPERVISOR_MODEL", "Qwen/Qwen3-32B")
    manager = os.environ.get("AYVEN_MANAGER_MODEL", "Qwen/Qwen3-30B-A3B")
    return [
        ModelSpec("ayven-calculator", "ayven", True, 0, 0, 0, 0, 0, 1, 0, "deterministic", (), ("calculation", "internal_door_quote"), True),
        ModelSpec(employee, "huggingface-or-openai-compat", True, 32768, 0.45, 0.4, 0.45, 0.2, 0.85, 16, "benchmark-default", ("EMPLOYEE",), ("extraction", "web_research", "football_tickets", "vending_prospects"), True),
        ModelSpec(supervisor, "gguf-or-openai-compat", True, 32768, 0.75, 0.55, 0.6, 0.6, 0.45, 24, "Q4_K_M-benchmark-default", ("SUPERVISOR",), ("verification", "audit"), True),
        ModelSpec(manager, "gguf-or-openai-compat", True, 32768, 0.7, 0.5, 0.55, 0.55, 0.5, 20, "Q4_K_M-benchmark-default", ("MANAGER",), ("planning", "synthesis"), True),
        ModelSpec("frontier-escalation", "openai-compatible-remote", False, 128000, 0.9, 0.8, 0.8, 1.0, 0.4, 0, "remote", ("ESCALATION",), ("unresolved_escalation",), _escalation_enabled()),
        ModelSpec("ayven-browser", "browser-use", True, 0, 0, 1, 0, 0.3, 0.4, 1, "local-chrome", ("TOOL",), ("browser",), True),
        ModelSpec("ayven-code-sandbox", "unshare", True, 0, 0, 0, 1, 0.1, 0.7, 0.5, "unshare-user-net-pid", ("TOOL",), ("code_sandbox",), True),
        ModelSpec(os.environ.get("AYVEN_CODING_MODEL", employee), "huggingface-or-openai-compat", True, 32768, 0.45, 0.5, 0.7, 0.2, 0.8, 16, "benchmark-default", ("CODING",), ("coding",), True),
    ]


def route_for(task_class: str, stage: str) -> dict:
    """Choose a model for a stage. Calculation never goes to an LLM."""
    specs = registry()
    if stage in ("calculation", "arithmetic") or (task_class == "internal_door_quote" and stage == "tools"):
        spec = next(item for item in specs if item.model_id == "ayven-calculator")
        return _public(spec, stage, "deterministic tool; no model call for the arithmetic")
    if stage in ("verification", "supervisor"):
        spec = next(item for item in specs if "SUPERVISOR" in item.roles)
        return _public(spec, stage, "independent audit uses the stronger local reasoning role")
    if stage in ("planning", "manager", "synthesis"):
        spec = next(item for item in specs if "MANAGER" in item.roles)
        return _public(spec, stage, "orchestration uses the manager role")
    if stage == "browser":
        spec = next(item for item in specs if item.model_id == "ayven-browser")
        return _public(spec, stage, "HTTP fetch is not enough (script, navigation, or interactive reading); browser is read-only")
    if stage == "code_sandbox":
        spec = next(item for item in specs if item.model_id == "ayven-code-sandbox")
        return _public(spec, stage, "work is computation beyond arithmetic, so the calculator is the wrong tool")
    if stage == "coding":
        spec = next(item for item in specs if "CODING" in item.roles)
        return _public(spec, stage, "coding-capable local model; the role stays separate from the model id")
    if stage == "escalation":
        spec = next(item for item in specs if "ESCALATION" in item.roles)
        reason = "frontier call allowed" if spec.enabled else "frontier escalation disabled; AYVEN_ALLOW_ESCALATION=0"
        return _public(spec, stage, reason)
    if task_class == "trivial":
        spec = next(item for item in specs if "EMPLOYEE" in item.roles)
        return _public(spec, stage, "trivial task; small local role, most stages skipped")
    spec = next(item for item in specs if "EMPLOYEE" in item.roles)
    return _public(spec, stage, "extraction and evidence-bounded drafting use the employee role")


def _public(spec: ModelSpec, stage: str, reason: str) -> dict:
    return {
        "model_id": spec.model_id,
        "provider": spec.provider,
        "local": spec.local,
        "context": spec.context,
        "reasoning": spec.reasoning,
        "tools": spec.tools,
        "coding": spec.coding,
        "cost": spec.cost,
        "speed": spec.speed,
        "memory_gb": spec.memory_gb,
        "quantisation": spec.quantisation,
        "roles": list(spec.roles),
        "enabled": spec.enabled,
        "stage": stage,
        "reason": reason,
        "paid_call": bool(spec.enabled and not spec.local),
    }
