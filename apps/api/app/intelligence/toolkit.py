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
            retrieved_at,extracted_content,error,metadata,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        ),
    )
    conn.commit()
    conn.close()
    return call_id


def invoke(agent_id: str, tool: str, package_id: str, fn: Callable[[], ToolResult], *, approved: bool = False) -> ToolResult:
    try:
        authorize(agent_id, tool, approved=approved)
    except PermissionDenied as exc:
        result = ToolResult(tool=tool, status="denied", error=str(exc), timestamp=now(), metadata={"capability": exc.capability})
        record_tool_call(package_id, agent_id, result)
        return result
    try:
        result = fn()
    except Exception as exc:
        result = ToolResult(tool=tool, status="error", error=f"{type(exc).__name__}: {exc}"[:400], timestamp=now())
    result.tool = tool
    result.timestamp = result.timestamp or now()
    result.extracted_content = strip_think(result.extracted_content)
    record_tool_call(package_id, agent_id, result)
    return result
