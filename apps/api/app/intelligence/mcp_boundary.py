"""Official MCP Python SDK client. Servers come from AYVEN_MCP_SERVERS.

Read tools can run after an Ayven permission check. Action tools need
approval. A server that does not start is marked unavailable.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from typing import Any

from .permissions import authorize

_READ_CAP = "web_search"
_ACTION_CAP = "external_contact"


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
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def status() -> dict:
    installed = sdk_installed()
    servers = configured_servers()
    if not installed:
        posture = "OPTIONAL_NOT_INSTALLED"
    elif not servers:
        posture = "INSTALLED_NO_SERVERS"
    else:
        posture = "CONFIGURED"
    return {
        "sdk": "modelcontextprotocol/python-sdk",
        "licence": "MIT",
        "installed": installed,
        "servers_configured": len(servers),
        "posture": posture,
        "note": "Connections are opened per call from AYVEN_MCP_SERVERS. Action tools stay approval-gated.",
    }


def classify_tool(name: str) -> str:
    lowered = name.lower()
    if any(word in lowered for word in ("send", "delete", "write", "purchase", "pay", "post", "create", "update")):
        return "action"
    return "read"


def _server_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "PYTHONNOUSERSITE", "AYVEN_DB")
    env = {key: os.environ[key] for key in keep if os.environ.get(key)}
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("HOME", "/tmp")
    return env


def prepare_call(agent_id: str, tool_name: str, approved: bool = False) -> dict:
    kind = classify_tool(tool_name)
    if not sdk_installed():
        return {"ok": False, "status": "OPTIONAL_NOT_INSTALLED", "kind": kind}
    tool_cap = _ACTION_CAP if kind == "action" else _READ_CAP
    try:
        authorize(agent_id, tool_cap, approved=True if kind == "action" else approved)
    except Exception as exc:
        return {"ok": False, "status": "denied", "kind": kind, "error": str(exc)}
    if kind == "action" and not approved:
        return {"ok": False, "status": "approval_required", "kind": kind}
    if not configured_servers():
        return {"ok": False, "status": "NO_SERVERS", "kind": kind}
    return {"ok": True, "status": "ready", "kind": kind}


def discover(server: dict | None = None) -> dict:
    """Connect, list tools and resources, then close. Does not execute tools."""
    target = server or (configured_servers()[:1] or [None])[0]
    if not sdk_installed():
        return {"ok": False, "status": "OPTIONAL_NOT_INSTALLED", "tools": [], "resources": []}
    if not target:
        return {"ok": False, "status": "NO_SERVERS", "tools": [], "resources": []}
    try:
        tools, resources = asyncio.run(_discover(target))
    except Exception as exc:
        return {"ok": False, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:300], "tools": [], "resources": [], "server": target.get("name")}
    return {"ok": True, "status": "connected", "tools": tools, "resources": resources, "server": target.get("name")}


def call_tool(agent_id: str, tool_name: str, arguments: dict | None = None, approved: bool = False, server: dict | None = None) -> dict:
    gate = prepare_call(agent_id, tool_name, approved=approved)
    record = {"tool": tool_name, "kind": gate["kind"], "agent_id": agent_id, "status": gate["status"]}
    if not gate["ok"]:
        record["ok"] = False
        return record
    target = server or _server_for(tool_name)
    if target is None:
        record.update({"ok": False, "status": "unavailable"})
        return record
    try:
        payload = asyncio.run(_call(target, tool_name, arguments or {}))
    except Exception as exc:
        record.update({"ok": False, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[:300]})
        return record
    record.update({"ok": True, "status": "ok", "result": payload[:2000]})
    return record


def _server_for(tool_name: str) -> dict | None:
    servers = configured_servers()
    for server in servers:
        names = server.get("tools") or []
        if tool_name in names:
            return server
    return servers[0] if servers else None


async def _discover(server: dict) -> tuple[list[str], list[str]]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=server.get("command") or sys.executable, args=list(server.get("args") or ["-m", "app.intelligence.mcp_local_server"]), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in getattr(tools, "tools", []) or []]
            resources: list[str] = []
            try:
                listed = await session.list_resources()
                resources = [str(item.uri) for item in getattr(listed, "resources", []) or []]
            except Exception:
                resources = []
            return names, resources


async def _call(server: dict, tool_name: str, arguments: dict) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=server.get("command") or sys.executable, args=list(server.get("args") or ["-m", "app.intelligence.mcp_local_server"]), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            chunks = []
            for block in getattr(result, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
            if not chunks:
                chunks.append(str(result))
            return "\n".join(chunks)
