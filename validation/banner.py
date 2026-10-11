"""Printed before any model download. Aborts are decided by the caller."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def git_sha(root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return "unknown"


def gpu_name() -> str:
    if not shutil.which("nvidia-smi"):
        return "none"
    try:
        return subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True,
        ).splitlines()[0].strip()
    except Exception:
        return "nvidia-smi-failed"


def vram() -> str:
    if not shutil.which("nvidia-smi"):
        return "none"
    try:
        return subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
            text=True,
        ).splitlines()[0].strip()
    except Exception:
        return "unknown"


def disk_free(path: Path) -> str:
    usage = shutil.disk_usage(path)
    return f"{usage.free / (1024 ** 3):.1f} GB free"


def collect(root: Path) -> dict:
    import sys

    api = root / "apps" / "api"
    if str(api) not in sys.path:
        sys.path.insert(0, str(api))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.intelligence.capabilities import frankenstein_status
    from app.intelligence.deps import dependency_health
    from app.version import __version__
    from validation.export_results import status_line

    caps = frankenstein_status()
    return {
        "version": __version__,
        "sha": git_sha(root),
        "gpu": gpu_name(),
        "vram": vram(),
        "disk": disk_free(root),
        "dependencies": dependency_health(),
        "components": caps,
        "export": status_line(),
    }


def render(info: dict) -> str:
    lines = [
        "============================================================",
        f"VERSION {info['version']}",
        f"SHA {info['sha']}",
        f"GPU {info['gpu']}",
        f"VRAM {info['vram']}",
        f"DISK {info['disk']}",
        f"DEPENDENCY HEALTH {'OK' if info['dependencies']['ok'] else 'NOT OK'}",
    ]
    for name, version in info["dependencies"]["packages"].items():
        lines.append(f"  {name}: {version}")
    for note in info["dependencies"]["conflicts"]:
        lines.append(f"  CONFLICT {note}")
    lines.append("CORE COMPONENT STATUS")
    for name, state in info["components"].items():
        lines.append(f"  {name}: {state}")
    lines.append(f"RESULT EXPORT STATUS {info['export']}")
    lines.append("============================================================")
    return "\n".join(lines)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    info = collect(root)
    print(render(info))
    gpu = os.environ.get("AYVEN_PREFLIGHT_GPU") == "1" or info["gpu"] != "none"
    from app.intelligence.capabilities import frankenstein_status
    from validation.preflight import continue_after_preflight, gating_failures

    caps = frankenstein_status()
    if gpu and not continue_after_preflight(caps, gpu=True):
        print("CORE CAPABILITY INACTIVE: " + ", ".join(gating_failures(caps)))
        print("Aborting before model download.")
        return 2
    if gpu and caps.get("llguidance") != "ACTIVE":
        print("CORE CAPABILITY INACTIVE: llguidance")
        print("Aborting before model download.")
        return 2
    if not info["dependencies"]["ok"]:
        print("Dependency health failed. Aborting before model download.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
