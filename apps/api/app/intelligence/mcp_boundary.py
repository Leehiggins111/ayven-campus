"""MCP boundary. The official SDK is optional and is not imported unless installed.

Read tools can be listed. Action tools stay approval-gated. No server is
contacted during normal research.
"""

from __future__ import annotations

import importlib.util
import json
import os

from .permissions import GATED, authorize


def sdk_installed() -> bool:
    return importlib.util.find_spec("mcp") is not None


def configured_servers() -> list[dict]:
    raw = os.environ.get("AYVEN_MCP_SERVERS", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def status() -> dict:
    return {
        "sdk": "modelcontextprotocol/python-sdk",
        "installed": sdk_installed(),
        "servers_configured": len(configured_servers()),
        "posture": "OPTIONAL_NOT_INSTALLED" if not sdk_installed() else "INSTALLED_NOT_CONNECTED",
        "note": "Ayven does not import MCP at startup. Connect a server later through this boundary.",
    }


def classify_tool(name: str) -> str:
    lowered = name.lower()
    if any(word in lowered for word in ("send", "delete", "write", "purchase", "pay", "post", "create", "update")):
        return "action"
    return "read"


def prepare_call(agent_id: str, tool_name: str, approved: bool = False) -> dict:
    kind = classify_tool(tool_name)
    if not sdk_installed():
        return {"ok": False, "status": "OPTIONAL_NOT_INSTALLED", "kind": kind}
    try:
        authorize(agent_id, "external_contact" if kind == "action" else "web_search", approved=approved)
    except Exception as exc:
        return {"ok": False, "status": "denied", "kind": kind, "error": str(exc), "gated": kind == "action" or True}
    if kind == "action" and not approved:
        return {"ok": False, "status": "approval_required", "kind": kind, "gated_capabilities": sorted(GATED)}
    return {"ok": False, "status": "NOT_CONNECTED", "kind": kind, "note": "SDK present but Ayven does not open a session in this build."}
