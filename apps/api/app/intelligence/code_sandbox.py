"""Local code execution. Arithmetic stays on the calculator.

Isolation actually in effect on this host: unshare --user --map-root-user
--net --pid --fork --mount-proc, plus timeout(1) outside the child and
python -I. Docker and bubblewrap are not required and are not used.

The new network namespace is the network control. The filesystem is not a
rootless container: host files remain visible. Resource limits are set at
start; a process that is root inside the user namespace can raise them.
The parent timeout cannot be raised from inside. Model code is never passed
to a host shell string.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .permissions import authorize
from .toolkit import ToolResult, now

_MAX_BYTES = 20_000


def isolation_level() -> str:
    if shutil.which("unshare") and shutil.which("timeout") and _unshare_works():
        return "unshare-user-net-pid"
    return "subprocess-timeout-no-namespace"


def _unshare_works() -> bool:
    try:
        probe = subprocess.run(
            ["unshare", "--user", "--map-root-user", "--net", "true"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def status() -> dict:
    level = isolation_level()
    return {
        "sandbox": level,
        "docker": bool(shutil.which("docker")),
        "bwrap": bool(shutil.which("bwrap")),
        "allow_code": os.environ.get("AYVEN_ALLOW_CODE", "0") == "1",
        "network": "off" if level == "unshare-user-net-pid" else "not-namespaced",
        "filesystem": "host-visible",
        "timeout": "parent timeout(1)",
        "note": "Calculator remains the benchmark arithmetic tool.",
    }


def run_code(agent_id: str, source: str, approved: bool = False, timeout: int = 5) -> ToolResult:
    try:
        authorize(agent_id, "code_exec", approved=approved)
    except Exception as exc:
        return ToolResult(tool="code_exec", status="denied", query=(source or "")[:200], error=str(exc), timestamp=now())
    if os.environ.get("AYVEN_ALLOW_CODE", "0") != "1":
        return ToolResult(
            tool="code_exec",
            status="disabled",
            query=(source or "")[:200],
            error="AYVEN_ALLOW_CODE=0. Use the calculator for arithmetic.",
            timestamp=now(),
            metadata={"sandbox": "disabled", "fallback": "calculator"},
        )
    if not source or len(source) > _MAX_BYTES:
        return ToolResult(tool="code_exec", status="error", error="empty_or_oversized_source", timestamp=now())
    level = isolation_level()
    try:
        stdout, stderr, code = _execute(source, timeout=timeout, level=level)
    except Exception as exc:
        return ToolResult(tool="code_exec", status="error", query=source[:200], error=f"{type(exc).__name__}: {exc}"[:300], timestamp=now(), metadata={"sandbox": level})
    ok = code == 0
    return ToolResult(
        tool="code_exec",
        status="ok" if ok else "error",
        query=source[:200],
        extracted_content=stdout[:4000],
        error=stderr[:1000] if not ok else "",
        timestamp=now(),
        metadata={"sandbox": level, "exit_code": code, "stderr": stderr[:500], "network": "off" if level == "unshare-user-net-pid" else "not-namespaced"},
    )


def _execute(source: str, timeout: int, level: str) -> tuple[str, str, int]:
    with tempfile.TemporaryDirectory(prefix="ayven-sandbox-") as folder:
        path = Path(folder) / "main.py"
        path.write_text(source, encoding="utf-8")
        python = os.environ.get("AYVEN_SANDBOX_PYTHON", "python3")
        cmd = [python, "-I", str(path)]
        if level == "unshare-user-net-pid":
            cmd = ["timeout", str(timeout), "unshare", "--user", "--map-root-user", "--net", "--pid", "--fork", "--mount-proc", *cmd]
        else:
            cmd = ["timeout", str(timeout), *cmd]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": folder,
            "LANG": "C.UTF-8",
        }
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3, env=env, check=False)
        return proc.stdout or "", proc.stderr or "", proc.returncode
