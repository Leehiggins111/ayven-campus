"""Pure preflight decisions for the GPU harness.

The shell installs what it can, then asks these functions whether to download
a model. The code sandbox is reported. It does not abort the run.
"""

from __future__ import annotations

GATING = (
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


def use_sudo(euid: int, sudo_on_path: bool) -> bool:
    """sudo only for an unprivileged user who actually has it. Root does not need it."""
    return euid != 0 and sudo_on_path


def chrome_commands(*, euid: int, sudo_on_path: bool, apt: bool) -> list[list[str]]:
    """Commands to try for a system Chrome. Empty when apt is missing. Never inserts sudo for root."""
    if not apt:
        return []
    prefix = ["sudo"] if use_sudo(euid, sudo_on_path) else []
    return [
        [*prefix, "apt-get", "update", "-qq"],
        [*prefix, "apt-get", "install", "-y", "-qq", "wget", "ca-certificates"],
    ]


def gating_failures(caps: dict) -> list[str]:
    """Inactive hard gates. code_sandbox REPORTED is not a failure."""
    failures = []
    for name in GATING:
        if caps.get(name) != "ACTIVE":
            failures.append(name)
    return failures


def continue_after_preflight(caps: dict, *, gpu: bool) -> bool:
    """A GPU run stops only for a hard gate. A weak sandbox does not stop it."""
    if not gpu:
        return True
    return not gating_failures(caps)


def isolation_report(level: str) -> str:
    if level in ("unshare-user-net-pid", "bubblewrap-unshare-net"):
        return f"code sandbox isolation: {level} (executable)"
    return f"code sandbox isolation: {level} (REPORTED, not used for code execution, not a gate)"
