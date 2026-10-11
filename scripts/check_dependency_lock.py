#!/usr/bin/env python3
"""Check that the pinned core set resolves and the known conflict pins are present.

Optional extras that pull click or pillow are installed only with constraints.txt,
then the pins are reapplied. This script fails if those pins disappear.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = (ROOT / "apps" / "api" / "constraints.txt").read_text(encoding="utf-8")
REQUIRED = (
    "click==8.3.3",
    "pillow==12.3.0",
    "pypdf==6.16.2",
    "typing-extensions>=4.12,<5",
    "pydantic==2.13.5",
    "llguidance==1.3.0",
)


def pins_ok() -> list[str]:
    missing = [pin for pin in REQUIRED if pin not in CONSTRAINTS]
    return missing


def resolve_core() -> int:
    requirements = [
        ROOT / "apps" / "api" / "requirements.txt",
        ROOT / "apps" / "api" / "requirements-frankenstein.txt",
        ROOT / "apps" / "api" / "requirements-documents.txt",
    ]
    command = [sys.executable, "-m", "pip", "install", "--dry-run"]
    for path in requirements:
        command.extend(["-r", str(path)])
    command.extend(["-c", str(ROOT / "apps" / "api" / "constraints.txt")])
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout[-2000:])
        sys.stderr.write(completed.stderr[-2000:])
    return completed.returncode


def main() -> int:
    missing = pins_ok()
    if missing:
        print("MISSING PINS: " + ", ".join(missing))
        return 1
    if "--pins-only" in sys.argv:
        print("PINS OK")
        return 0
    code = resolve_core()
    print("CORE RESOLVES" if code == 0 else "CORE CONFLICT")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
