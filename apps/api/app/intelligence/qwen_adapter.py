"""Optional Qwen-Agent adapter.

The default loop does not import qwen_agent. Enable with AYVEN_USE_QWEN_AGENT=1
only when that package is installed. Ayven still owns planning, claims, and audit.
"""

from __future__ import annotations

import importlib.util
import os


def enabled() -> bool:
    return os.environ.get("AYVEN_USE_QWEN_AGENT", "0") == "1"


def available() -> bool:
    return importlib.util.find_spec("qwen_agent") is not None


def status() -> dict:
    return {
        "repo": "QwenLM/Qwen-Agent",
        "licence": "Apache-2.0",
        "enabled": enabled(),
        "installed": available(),
        "used_for_inference": False,
        "note": "Patterns adopted: tool schema, multi-step tool use, stop when evidence is enough. Internals are not imported.",
    }


def run_tool_loop(*_args, **_kwargs) -> None:
    """Reserved hook. Returns None so the caller keeps Ayven's loop."""
    if not enabled() or not available():
        return None
    return None
