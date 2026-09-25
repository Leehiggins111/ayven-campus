"""Explicit fallbacks. Missing evidence is a gap, never a silent model memory fill."""

from __future__ import annotations


def on_http_result(*, status: str, error: str, browser_available: bool, browser_permitted: bool) -> dict:
    if status == "ok":
        return {"action": "keep_http", "launch_browser": False, "gap": ""}
    if error == "js_wall" and browser_available and browser_permitted:
        return {"action": "browser", "launch_browser": True, "gap": ""}
    if error == "js_wall":
        why = "browser_not_permitted" if not browser_permitted else "browser_unavailable"
        return {"action": "gap", "launch_browser": False, "gap": f"Page needed a browser ({why}). No model-memory fallback was used."}
    return {"action": "record_failure", "launch_browser": False, "gap": ""}


def on_runtime_error(exc: BaseException) -> dict:
    return {"runtime": "native", "error": f"{type(exc).__name__}: {exc}", "safe": True}


def on_mcp_down(server_name: str, error: str) -> dict:
    return {"server": server_name, "tools": "unavailable", "error": error}


def on_sandbox_unavailable() -> dict:
    return {"use": "calculator", "reason": "sandbox unavailable; arithmetic stays on the calculator, and non-arithmetic work is a gap"}
