"""Shell scripts stay LF on every platform.

Before: with no .gitattributes, a Windows checkout or `git archive` under core.autocrlf=true produced CRLF
scripts, and bash refused them at `set -euo pipefail` ("pipefail\\r: invalid option name").
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__"}


def shell_scripts():
    return [p for p in ROOT.rglob("*.sh") if not SKIP.intersection(p.relative_to(ROOT).parts)]


def test_gitattributes_forces_lf_for_shell_scripts():
    rules = [line.strip() for line in (ROOT / ".gitattributes").read_text().splitlines()]
    assert "*.sh text eol=lf" in rules


def test_no_shell_script_has_crlf():
    scripts = shell_scripts()
    assert scripts
    assert [str(p.relative_to(ROOT)) for p in scripts if b"\r" in p.read_bytes()] == []


@pytest.mark.skipif(not shutil.which("git") or not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_every_tracked_shell_script_checks_out_lf():
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.sh"], capture_output=True, text=True,
                             timeout=60, check=True).stdout.split()
    assert tracked
    for path in tracked:
        attr = subprocess.run(["git", "-C", str(ROOT), "check-attr", "eol", "--", path], capture_output=True,
                              text=True, timeout=60, check=True).stdout
        assert attr.strip().endswith("eol: lf"), (path, attr)
