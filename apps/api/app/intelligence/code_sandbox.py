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
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .permissions import authorize
from .toolkit import ToolResult, now

_MAX_BYTES = 20_000


def isolation_choice(unshare_ok: bool, bwrap_ok: bool) -> tuple[str, bool]:
    """Level name, and whether that level may execute model code.

    The weakest label is reported only. It does not run code.
    """
    if unshare_ok:
        return "unshare-user-net-pid", True
    if bwrap_ok:
        return "bubblewrap-unshare-net", True
    return "rlimit-subprocess-no-network-unverified", False


def isolation_level() -> str:
    return isolation_choice(_unshare_works(), _bwrap_works())[0]


def isolation_executable() -> bool:
    return isolation_choice(_unshare_works(), _bwrap_works())[1]


def _bwrap_works() -> bool:
    if not shutil.which("bwrap") or not shutil.which("timeout"):
        return False
    try:
        probe = subprocess.run(
            ["bwrap", "--unshare-net", "--", "true"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


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
        "note": "Calculator remains the benchmark arithmetic tool. rlimit alone is not a production sandbox and is not used to execute code.",
        "evaluated": {
            "daytonaio/daytona": "REJECTED",
            "gvisor": "OPTIONAL",
            "nsjail": "OPTIONAL",
            "firecracker": "OPTIONAL",
            "bubblewrap": "ADAPTED",
            "docker": "OPTIONAL",
        },
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
    level, executable = isolation_choice(_unshare_works(), _bwrap_works())
    if not executable:
        return ToolResult(
            tool="code_exec",
            status="disabled",
            query=(source or "")[:200],
            error=f"{level} is reported only and is not used for code execution. Use the calculator for arithmetic.",
            timestamp=now(),
            metadata={"sandbox": level, "executable": False},
        )
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


def _safe_filename(name: str) -> str:
    cleaned = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}\.py", cleaned or ""):
        return ""
    return cleaned


def _tests_are_meaningful(source: str) -> bool:
    """A test that cannot fail is not a test."""
    text = source or ""
    asserts = re.findall(r"^\s*assert\s+(.+)$", text, re.M)
    if not asserts:
        return False
    if all(re.fullmatch(r"True|1|''|\"\"", item.strip()) for item in asserts):
        return False
    if all("__file__" in item for item in asserts):
        return False
    return True


def run_files(files: dict[str, str], test_name: str = "test_main.py", timeout: int = 20) -> dict:
    """Run a generated test file.

    Namespace isolation is used when this host has it. A host without unshare
    or bwrap, including the Windows runner, uses a timeout-bounded python -I
    in a temporary directory. That weaker level is recorded. General run_code
    still refuses to execute at that level.
    """
    if os.environ.get("AYVEN_ALLOW_CODE", "0") != "1":
        return {
            "status": "disabled",
            "passed": False,
            "stdout": "",
            "stderr": "AYVEN_ALLOW_CODE=0",
            "sandbox": "disabled",
            "exit_code": None,
        }
    safe: dict[str, str] = {}
    for name, source in (files or {}).items():
        cleaned = _safe_filename(name)
        if not cleaned or not source or len(source) > _MAX_BYTES:
            continue
        safe[cleaned] = source if source.endswith("\n") else source + "\n"
    if test_name not in safe or "main.py" not in safe:
        return {
            "status": "error",
            "passed": False,
            "stdout": "",
            "stderr": "The model did not write main.py and test_main.py.",
            "sandbox": "not-run",
            "exit_code": None,
        }
    if not _tests_are_meaningful(safe[test_name]):
        return {
            "status": "error",
            "passed": False,
            "stdout": "",
            "stderr": "The test does not check the program.",
            "sandbox": "not-run",
            "exit_code": None,
        }
    level, executable = isolation_choice(_unshare_works(), _bwrap_works())
    recorded = level if executable else "timeout-subprocess-tempdir"
    try:
        stdout, stderr, code = _execute_files(safe, test_name, timeout=timeout, level=level if executable else recorded)
    except Exception as exc:
        return {
            "status": "error",
            "passed": False,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}"[:500],
            "sandbox": recorded,
            "exit_code": None,
        }
    passed = code == 0
    return {
        "status": "ok" if passed else "error",
        "passed": passed,
        "stdout": (stdout or "")[:4000],
        "stderr": (stderr or "")[:2000],
        "sandbox": recorded,
        "exit_code": code,
    }


def _python_on_path(folder: str, env: dict) -> None:
    """A test that runs `python main.py` still runs where the executable is python3."""
    path = env.get("PATH", "")
    if shutil.which("python", path=path):
        return
    py3 = shutil.which("python3", path=path)
    if not py3:
        return
    wrapper = Path(folder) / "python"
    wrapper.write_text(f"#!/bin/sh\nexec {py3} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    env["PATH"] = folder + os.pathsep + path


def _execute_files(files: dict[str, str], test_name: str, timeout: int, level: str) -> tuple[str, str, int]:
    module = test_name[:-3]
    runner = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
        f"import {module}\n"
    )
    with tempfile.TemporaryDirectory(prefix="ayven-sandbox-") as folder:
        for name, source in files.items():
            (Path(folder) / name).write_text(source, encoding="utf-8")
        (Path(folder) / "run_check.py").write_text(runner, encoding="utf-8")
        python = os.environ.get("AYVEN_SANDBOX_PYTHON") or shutil.which("python3") or shutil.which("python") or "python3"
        cmd = [python, "-I", "run_check.py"]
        if level == "unshare-user-net-pid":
            cmd = ["unshare", "--user", "--map-root-user", "--net", "--pid", "--fork", "--mount-proc", *cmd]
        elif level == "bubblewrap-unshare-net":
            cmd = ["bwrap", "--unshare-net", "--unshare-pid", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--proc", "/proc", "--dev", "/dev", "--", *cmd]
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": folder,
            "LANG": "C.UTF-8",
        }
        _python_on_path(folder, env)
        proc = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=timeout + 3, env=env, check=False)
        return proc.stdout or "", proc.stderr or "", proc.returncode


def _execute(source: str, timeout: int, level: str) -> tuple[str, str, int]:
    with tempfile.TemporaryDirectory(prefix="ayven-sandbox-") as folder:
        path = Path(folder) / "main.py"
        path.write_text(source, encoding="utf-8")
        python = os.environ.get("AYVEN_SANDBOX_PYTHON", "python3")
        cmd = [python, "-I", str(path)]
        if level == "unshare-user-net-pid":
            cmd = ["timeout", str(timeout), "unshare", "--user", "--map-root-user", "--net", "--pid", "--fork", "--mount-proc", *cmd]
        elif level == "bubblewrap-unshare-net":
            cmd = ["timeout", str(timeout), "bwrap", "--unshare-net", "--unshare-pid", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--proc", "/proc", "--dev", "/dev", "--", *cmd]
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
