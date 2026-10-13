"""Structured tool results. The model may choose a tool; the tool does the work."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from ..db import connect
from .permissions import PermissionDenied, authorize
from .think import strip_think


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_id() -> str:
    from .observability import current_trace

    return current_trace()


@dataclass
class ToolResult:
    tool: str
    status: str
    query: str = ""
    source_url: str = ""
    source_title: str = ""
    timestamp: str = ""
    extracted_content: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["extracted_content"] = strip_think(data["extracted_content"])
        data["error"] = strip_think(data["error"])
        return data


def valid_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.netloc.lower()
    if not host or " " in url or ".." in host:
        return False
    if host in {"example.com", "localhost", "127.0.0.1"}:
        return False
    return True


def record_tool_call(package_id: str, agent_id: str, result: ToolResult) -> str:
    call_id = str(uuid.uuid4())
    payload = result.as_dict()
    conn = connect()
    conn.execute(
        """INSERT INTO tool_calls(
            id,package_id,agent_id,tool,status,query,source_url,source_title,
            retrieved_at,extracted_content,error,metadata,created_at,trace_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            call_id,
            package_id,
            agent_id,
            result.tool,
            result.status,
            result.query,
            result.source_url,
            result.source_title,
            result.timestamp or now(),
            payload["extracted_content"][:8000],
            payload["error"][:1000],
            json.dumps(result.metadata),
            now(),
            (result.metadata or {}).get("trace_id") or _trace_id(),
        ),
    )
    conn.commit()
    conn.close()
    return call_id


def _project_of(package_id: str) -> str:
    if not package_id:
        return ""
    conn = connect()
    row = conn.execute("SELECT project_id FROM work_packages WHERE id=?", (package_id,)).fetchone()
    conn.close()
    return (row["project_id"] if row else "") or ""


def invoke(agent_id: str, tool: str, package_id: str, fn: Callable[[], ToolResult], *, approved: bool = False) -> ToolResult:
    try:
        authorize(agent_id, tool, approved=approved)
    except PermissionDenied as exc:
        result = ToolResult(tool=tool, status="denied", error=str(exc), timestamp=now(), metadata={"capability": exc.capability})
        record_tool_call(package_id, agent_id, result)
        return result
    from .steps import agent_for, current_owner, tool_allowed

    owner = current_owner()
    if owner and agent_id == agent_for(owner) and not tool_allowed(owner, tool):
        result = ToolResult(
            tool=tool,
            status="denied",
            error=f"{owner} cannot call {tool} on this step",
            timestamp=now(),
            metadata={"step_owner": owner},
        )
        record_tool_call(package_id, agent_id, result)
        return result
    from .recovery import run_retries

    def attempt(_number: int):
        result = fn()
        if not isinstance(result, ToolResult) or result.status == "malformed":
            return {"ok": False, "error": "malformed tool result", "result": result if isinstance(result, ToolResult) else None}
        return {"ok": True, "result": result}

    outcome = run_retries(
        attempt,
        stage=f"tool-{tool}",
        project_id=_project_of(package_id),
        task_id=package_id or "",
    )
    if outcome["ok"]:
        result = outcome["result"]["result"]
    else:
        last = (outcome.get("attempts") or [{}])[-1]
        result = ToolResult(
            tool=tool,
            status="error",
            error=(last.get("error") or "retries_exhausted")[:400],
            timestamp=now(),
            metadata={"attempts": len(outcome.get("attempts") or [])},
        )
    result.tool = tool
    result.timestamp = result.timestamp or now()
    result.extracted_content = strip_think(result.extracted_content)
    result.metadata = dict(result.metadata or {})
    if "attempts" not in result.metadata:
        result.metadata["attempts"] = len(outcome.get("attempts") or [])
    result.metadata["retries"] = max(0, len(outcome.get("attempts") or []) - 1)
    record_tool_call(package_id, agent_id, result)
    return result
