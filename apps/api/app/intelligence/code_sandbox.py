"""Local-first code execution boundary.

smolagents and E2B show the shape of a code agent. Ayven does not install
them. Arbitrary Python, Docker, and E2B are off. The only enabled execution
is the restricted arithmetic tool.
"""

from __future__ import annotations

import os

from .calc import CalcError, eval_arithmetic
from .permissions import authorize
from .toolkit import ToolResult, now


def run_code(agent_id: str, source: str, approved: bool = False) -> ToolResult:
    try:
        authorize(agent_id, "code_exec", approved=approved)
    except Exception as exc:
        return ToolResult(tool="code_exec", status="denied", query=source[:200], error=str(exc), timestamp=now())
    if os.environ.get("AYVEN_ALLOW_CODE", "0") != "1":
        return ToolResult(
            tool="code_exec",
            status="disabled",
            query=source[:200],
            error="AYVEN_ALLOW_CODE=0. Use the calculator for arithmetic. No Docker or E2B sandbox is started.",
            timestamp=now(),
            metadata={"sandbox": "none", "pattern_studied": ["huggingface/smolagents", "e2b"]},
        )
    try:
        value = eval_arithmetic(source)
    except CalcError as exc:
        return ToolResult(tool="code_exec", status="error", query=source[:200], error=str(exc), timestamp=now())
    return ToolResult(tool="code_exec", status="ok", query=source[:200], extracted_content=value, timestamp=now(), metadata={"sandbox": "ast-arithmetic-only"})
