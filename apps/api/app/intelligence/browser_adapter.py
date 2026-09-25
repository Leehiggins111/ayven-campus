"""Browser Use sits behind this adapter. It is not installed and not required."""

from __future__ import annotations

import importlib.util

from .permissions import authorize
from .toolkit import ToolResult, invoke, now


def available() -> bool:
    return importlib.util.find_spec("browser_use") is not None


def open_page(agent_id: str, package_id: str, url: str, approved: bool = False) -> ToolResult:
    def _run() -> ToolResult:
        authorize(agent_id, "browser", approved=approved)
        if not available():
            return ToolResult(
                tool="browser",
                status="unavailable",
                query=url,
                source_url=url,
                error="browser_use_not_installed",
                timestamp=now(),
                metadata={"adapter": "browser-use", "selected": False},
            )
        return ToolResult(tool="browser", status="error", error="adapter_loaded_but_not_wired", timestamp=now())

    try:
        authorize(agent_id, "browser", approved=approved)
    except Exception as exc:
        result = ToolResult(tool="browser", status="denied", query=url, error=str(exc), timestamp=now())
        invoke(agent_id, "browser", package_id, lambda: result)
        return result
    return invoke(agent_id, "browser", package_id, _run)
