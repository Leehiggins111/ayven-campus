"""What the planner is allowed to select. Health checks are real imports or probes."""

from __future__ import annotations

import importlib.util
import os
import sqlite3

from .code_sandbox import isolation_level
from . import qwen_adapter


def _ok(name: str, kind: str, description: str, *, permissions: list[str], available: bool, cost: str, where: str, effect: str, depends: list[str], tasks: list[str], health: str) -> dict:
    return {
        "name": name,
        "type": kind,
        "description": description,
        "permissions": permissions,
        "available": available,
        "cost_type": cost,
        "location": where,
        "effect": effect,
        "dependencies": depends,
        "health": health,
        "tasks": tasks,
    }


def health() -> list[dict]:
    calc_ok = False
    calc_detail = "failed"
    try:
        from .calc import eval_arithmetic

        calc_ok = eval_arithmetic("2+2") == "4.00"
        calc_detail = "2+2=4.00" if calc_ok else "unexpected"
    except Exception as exc:
        calc_detail = type(exc).__name__

    qwen_ok = qwen_adapter.available()
    try:
        if qwen_ok:
            from qwen_agent.agents.fncall_agent import FnCallAgent  # noqa: F401

            qwen_detail = f"import FnCallAgent {qwen_adapter.status().get('version')}"
        else:
            qwen_detail = "not installed"
    except Exception as exc:
        qwen_ok = False
        qwen_detail = type(exc).__name__

    browser_mod = importlib.util.find_spec("browser_use") is not None
    from .browser_adapter import chrome_path

    browser_ok = browser_mod and bool(chrome_path())
    browser_detail = chrome_path() or "chrome missing"

    mcp_ok = importlib.util.find_spec("mcp.client.stdio") is not None
    mcp_detail = "mcp.client.stdio" if mcp_ok else "not installed"
    if mcp_ok and os.environ.get("AYVEN_MCP_SERVERS"):
        from .mcp_boundary import discover

        found = discover()
        mcp_ok = bool(found.get("ok"))
        mcp_detail = found.get("status") or found.get("error") or mcp_detail

    from .code_sandbox import isolation_choice, isolation_executable, _bwrap_works, _unshare_works

    level, _sandbox_exec = isolation_choice(_unshare_works(), _bwrap_works())
    sandbox_ok = isolation_executable()
    memory_ok = True
    try:
        from ..db import connect

        conn = connect()
        conn.execute("SELECT 1 FROM memories LIMIT 1")
        conn.close()
        memory_detail = "sqlite memories"
    except sqlite3.Error as exc:
        memory_ok = False
        memory_detail = str(exc)

    skills_ok = False
    try:
        from .skills import discover

        skills_ok = len(discover()) >= 1
        skills_detail = "SKILL.md catalogue"
    except Exception as exc:
        skills_detail = type(exc).__name__

    return [
        _ok("web_search", "tool", "DuckDuckGo HTML search", permissions=["READ_WEB"], available=True, cost="free", where="remote-http", effect="read", depends=["httpx"], tasks=["web_search"], health="httpx client"),
        _ok("http_fetch", "tool", "Cheap HTTP fetch", permissions=["READ_WEB"], available=True, cost="free", where="remote-http", effect="read", depends=["httpx"], tasks=["http_fetch"], health="httpx client"),
        _ok("browser", "tool", "Browser Use read-only navigation", permissions=["BROWSE_WEB"], available=browser_ok, cost="local-cpu", where="local", effect="read", depends=["browser-use", "chrome"], tasks=["browser"], health=browser_detail),
        _ok("calculator", "tool", "Restricted arithmetic", permissions=["RUN_CALC"], available=calc_ok, cost="free", where="local", effect="read", depends=[], tasks=["calculator"], health=calc_detail),
        _ok("code_sandbox", "tool", "Isolated Python for non-arithmetic work", permissions=["RUN_CODE"], available=sandbox_ok, cost="local-cpu", where="local", effect="read", depends=["unshare"], tasks=["code_sandbox"], health=level),
        _ok("mcp", "tool", "MCP client for configured servers", permissions=["READ_WEB"], available=mcp_ok, cost="free", where="local", effect="read", depends=["mcp"], tasks=["mcp"], health=mcp_detail),
        _ok("memory", "tool", "SQLite memory with provenance", permissions=["READ_WEB"], available=memory_ok, cost="free", where="local", effect="read", depends=["sqlite"], tasks=["memory"], health=memory_detail),
        _ok("qwen_agent", "runtime", "Qwen-Agent function-calling loop", permissions=["READ_WEB"], available=qwen_ok and qwen_adapter.runtime_mode() == "qwen-agent", cost="local-model", where="local", effect="read", depends=["qwen-agent"], tasks=["agent_runtime"], health=qwen_detail),
        _ok("skills", "control", "Selected SKILL.md instructions", permissions=[], available=skills_ok, cost="free", where="local", effect="read", depends=["skills/"], tasks=["skills"], health=skills_detail),
        _ok("research_loop", "control", "Plan, search, read, review, gap, stop", permissions=["READ_WEB"], available=True, cost="free", where="local", effect="read", depends=["research.py"], tasks=["research"], health="review hook present"),
        _ok("claim_ledger", "control", "Claim statuses and evidence links", permissions=[], available=True, cost="free", where="local", effect="read", depends=["sqlite"], tasks=["claims"], health="claims module"),
        _ok("critic", "control", "Draft critique", permissions=[], available=True, cost="free", where="local", effect="read", depends=[], tasks=["critic"], health="critic module"),
        _ok("verifier", "control", "Deterministic verification", permissions=[], available=True, cost="free", where="local", effect="read", depends=[], tasks=["verifier"], health="verifier module"),
        _ok("supervisor_tools", "control", "Independent calculator and evidence inspection", permissions=["READ_WEB", "RUN_CALC", "BROWSE_WEB"], available=True, cost="free", where="local", effect="read", depends=["research-sup"], tasks=["supervisor"], health="research-sup caps"),
        _ok("manager_judgement", "control", "Manager proposal with safety veto", permissions=[], available=True, cost="local-model", where="local", effect="read", depends=["resolution.py"], tasks=["manager"], health="veto present"),
        _ok("coding_agent", "specialist", "Future software specialist. Not installed.", permissions=["RUN_CODE"], available=False, cost="none", where="local", effect="write", depends=[], tasks=["coding_agent"], health="rejected-as-host"),
    ]


def available_names() -> list[str]:
    return [item["name"] for item in health() if item["available"]]


def select_tools(requested: list[str]) -> list[str]:
    """Drop anything that is not healthy. Unavailable tools must not look usable."""
    alive = set(available_names())
    aliases = {"fetch_page": "http_fetch", "web_search": "web_search", "calculator": "calculator", "browser": "browser", "code_exec": "code_sandbox"}
    chosen = []
    for name in requested:
        key = aliases.get(name, name)
        if key in alive or name in alive:
            chosen.append(name)
    return chosen


GATING_CAPABILITIES = (
    "qwen_agent",
    "browser",
    "mcp",
    "skills",
    "research_loop",
    "claim_ledger",
    "critic",
    "verifier",
    "supervisor_tools",
    "manager_judgement",
    "memory",
    "routing",
    "registry",
)


def frankenstein_status() -> dict:
    rows = {item["name"]: item for item in health()}
    report = {name: "ACTIVE" if rows.get(name, {}).get("available") else "INACTIVE" for name in GATING_CAPABILITIES if name in rows}
    report["routing"] = "ACTIVE"
    report["registry"] = "ACTIVE"
    level = isolation_level()
    report["isolation"] = level
    report["code_sandbox"] = "ACTIVE" if level in ("unshare-user-net-pid", "bubblewrap-unshare-net") else "REPORTED"
    report["all_core_active"] = all(report.get(name) == "ACTIVE" for name in GATING_CAPABILITIES)
    return report
