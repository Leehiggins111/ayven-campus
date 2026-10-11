"""Dependency health. Conflicts are reported. They do not silently change packages."""

from __future__ import annotations

import importlib.util


def _version(name: str) -> str:
    try:
        import importlib.metadata as meta

        return meta.version(name)
    except Exception:
        return "not-installed"


def dependency_health() -> dict:
    wanted = ("fastapi", "pydantic", "httpx", "llguidance", "qwen-agent", "mcp", "browser-use", "starlette", "typing-extensions", "click")
    packages = {name: _version(name) for name in wanted}
    conflicts = []
    if packages["browser-use"] != "not-installed" and packages["click"] not in ("not-installed", "unknown"):
        # browser-use 0.13 has been seen to disagree with a click upgrade pulled by llama-cpp.
        conflicts.append("If browser-use fails to import, reinstall with apps/api/constraints.txt so click is not upgraded out from under it.")
    if importlib.util.find_spec("llama_cpp") is not None and packages["typing-extensions"] == "not-installed":
        conflicts.append("llama-cpp is present without typing-extensions. Reinstall the core requirements before the GPU extra.")
    return {
        "packages": packages,
        "conflicts": conflicts,
        "ok": packages["fastapi"] != "not-installed" and packages["pydantic"] != "not-installed" and packages["llguidance"] != "not-installed",
        "installer": "pip install -r apps/api/requirements.txt -c apps/api/constraints.txt",
    }
