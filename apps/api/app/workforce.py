"""Workforce entry point.

Employees, the supervisor, and the manager still exist. The intelligence
engine decides the plan, tools, claims, and audits.
"""

from __future__ import annotations

EMPLOYEES = ["research-e1", "research-e2", "research-e3"]
SUPERVISOR = "research-sup"
MANAGER = "research-mgr"


def run_objective(project_id: str, objective: str, task_id: str | None = None) -> str:
    from .intelligence.execution import run_objective as run_intelligence

    return run_intelligence(project_id, objective, task_id)
