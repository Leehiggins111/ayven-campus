"""One owned step at a time.

The director's task list is the coordination record. A step starts only when
its dependency is done. The model is shown that step and its acceptance
criteria. Tool names are allowlisted per owner, and arguments go through the
same validator as a live tool call. Timeouts and retries use the normal
bounded runner.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar

from .permissions import PermissionDenied, authorize

_OWNER: ContextVar[str] = ContextVar("ayven_step_owner", default="")

# Subset of what the role may hold. Sales drafts a listing and does not call a tool.
OWNER_AGENT = {
    "research": "research-e1",
    "commercial": "research-e2",
    "creation": "research-e3",
    "qa": "research-sup",
    "sales": "research-mgr",
    "director": "milo",
}

OWNER_TOOLS = {
    "research": {"web_search", "fetch_page", "calculator"},
    "commercial": {"calculator"},
    "creation": {"web_search", "fetch_page", "calculator"},
    "qa": {"web_search", "fetch_page", "calculator", "record_review"},
    "sales": set(),
    "director": {"web_search"},
}

_PUBLISHED = re.compile(r"\b(i published|published the listing|published it)\b", re.I)
_INVENTED_PRICE = re.compile(r"\b(invented price|the price is £\s*\d|the price is \$\s*\d)\b", re.I)


class StepDenied(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class DependencyBlocked(StepDenied):
    pass


def bind_owner(owner: str):
    return _OWNER.set(owner or "")


def reset_owner(token) -> None:
    _OWNER.reset(token)


def current_owner() -> str:
    return _OWNER.get()


def agent_for(owner: str) -> str:
    return OWNER_AGENT.get(owner, owner)


def tools_for(owner: str) -> set[str]:
    return set(OWNER_TOOLS.get(owner, set()))


def tool_allowed(owner: str, tool: str) -> bool:
    return tool in tools_for(owner)


def current_step(project_id: str) -> dict | None:
    from ..goals import list_tasks

    if not project_id:
        return None
    tasks = list_tasks(project_id)
    done = {task["id"] for task in tasks if task["status"] == "done"}
    for task in tasks:
        if task["status"] not in {"queued", "in_progress", "rework"}:
            continue
        if task.get("depends_on") and task["depends_on"] not in done:
            continue
        return task
    return None


def review_step(project_id: str) -> dict | None:
    from ..goals import list_tasks

    if not project_id:
        return None
    tasks = list_tasks(project_id)
    done = [task for task in tasks if task["status"] == "done"]
    if done:
        return done[-1]
    return current_step(project_id)


def scoped_prompt(task: dict | None) -> str:
    """One step, its criteria, and its tools. Later owners are out of scope."""
    if not task:
        return ""
    tools = ", ".join(sorted(tools_for(task["owner"]))) or "none"
    criteria = "\n".join(f"- {item}" for item in task.get("acceptance_criteria") or [])
    return (
        f"Your only step is {task['title']}. You are {task['owner']}. "
        "Do this step and stop. Do not start the next owner's work.\n"
        f"Acceptance criteria:\n{criteria}\n"
        f"Tools you may call: {tools}."
    )


def review_prompt(task: dict | None) -> str:
    if not task:
        return ""
    criteria = "\n".join(f"- {item}" for item in task.get("acceptance_criteria") or [])
    return (
        f"Review only this step: {task['title']} ({task['owner']}). "
        "A miss against these criteria is a fail. Do not take the next owner's work.\n"
        f"{criteria}"
    )


def _load(task_id: str) -> dict:
    from ..db import connect
    from ..goals import _task

    conn = connect()
    row = conn.execute("SELECT * FROM owned_tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    if not row:
        raise StepDenied("task not found")
    return _task(row)


def _dependency_ready(task: dict) -> None:
    depends = task.get("depends_on") or ""
    if not depends:
        return
    prior = _load(depends)
    if prior["status"] != "done":
        raise DependencyBlocked(f"{task['id']} waits for {depends}")


def start_task(task_id: str) -> dict:
    """Mark the step in progress. A later owner cannot start early."""
    task = _load(task_id)
    if task["status"] == "done":
        return task
    _dependency_ready(task)
    if task["status"] == "queued":
        from ..goals import set_task_status

        set_task_status(task_id, "in_progress")
    return _load(task_id)


def constraint_breach(output: str, criteria: list[str]) -> list[str]:
    """Hard constraints the step is not allowed to break."""
    text = output or ""
    if not text.strip():
        return ["output is empty"]
    lowered = text.lower()
    missed = []
    for item in criteria or []:
        rule = item.lower()
        if "publish nothing" in rule or "nothing is published" in rule or "do not publish" in rule:
            if _PUBLISHED.search(text):
                missed.append(item)
        if "do not invent a price" in rule or "prices come from" in rule:
            if _INVENTED_PRICE.search(text) and "http" not in lowered and "[f" not in lowered:
                missed.append(item)
    return missed


def complete_task(task_id: str, output: str) -> dict:
    """Finish the step, then hand the next owner a durable record. Do not skip ahead."""
    task = _load(task_id)
    if task["status"] == "done":
        return {"ok": True, "replayed": True, "next": current_step(task["project_id"])}
    _dependency_ready(task)
    missed = constraint_breach(output, task.get("acceptance_criteria") or [])
    if missed:
        return {"ok": False, "missing": missed, "status": task["status"]}
    from ..goals import handover, set_task_status

    set_task_status(task_id, "done")
    nxt = current_step(task["project_id"])
    if nxt and nxt["status"] == "queued":
        handover(
            task["project_id"],
            nxt["id"],
            task["owner"],
            nxt["owner"],
            list(nxt.get("acceptance_criteria") or []),
        )
        nxt = current_step(task["project_id"])
    return {"ok": True, "replayed": False, "next": nxt}


def run_step(task_id: str, fn, *, timeout: float | None = None, max_attempts: int = 3) -> dict:
    """Run one step through the normal timeout and retry path, then complete it."""
    from .recovery import run_bounded, run_retries

    start_task(task_id)

    def once(_attempt: int):
        result, error = run_bounded(f"step-{task_id}", fn, timeout=timeout)
        if error or not isinstance(result, str) or not result.strip():
            return {"ok": False, "error": error or "empty step output", "result": result}
        return {"ok": True, "result": result}

    outcome = run_retries(once, stage=f"step-{task_id}", max_attempts=max_attempts, task_id=task_id)
    if not outcome["ok"]:
        return {"ok": False, "attempts": outcome.get("attempts") or [], "missing": ["retries exhausted"]}
    finished = complete_task(task_id, outcome["result"]["result"])
    finished["attempts"] = outcome.get("attempts") or []
    return finished


def check_call(owner: str, tool: str, arguments) -> dict:
    """Allowlist, role permission, then the shared argument validator."""
    if not tool_allowed(owner, tool):
        raise StepDenied(f"{owner} cannot call {tool} on this step")
    try:
        authorize(agent_for(owner), tool, approved=False)
    except PermissionDenied as exc:
        raise StepDenied(str(exc)) from exc
    from .boundary import validate_tool_call

    payload = arguments if isinstance(arguments, str) else json.dumps(arguments)
    gate = validate_tool_call({"name": "ayven_tool", "arguments": json.dumps({"tool": tool, "payload": payload})})
    if not gate.ok:
        raise StepDenied(gate.error or "invalid arguments")
    return gate.call
