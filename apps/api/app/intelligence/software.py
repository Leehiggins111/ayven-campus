"""A model-written program. The files are stored only when their tests pass."""

from __future__ import annotations

import re

_FILE_LINE = re.compile(r"^FILE:\s*([A-Za-z0-9_.-]+)\s*$", re.I)
_LAUNCH_LINE = re.compile(r"^LAUNCH:\s*(.+?)\s*$", re.I)
_FENCE = re.compile(r"^```")


def _strip_fence(body: str) -> str:
    lines = (body or "").splitlines()
    if lines and _FENCE.match(lines[0].strip()):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_program(text: str) -> tuple[dict[str, str], str]:
    """FILE blocks and one LAUNCH line. Anything else is not a file."""
    files: dict[str, list[str]] = {}
    order: list[str] = []
    current = ""
    launch = ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        file_match = _FILE_LINE.match(stripped)
        if file_match:
            current = file_match.group(1)
            files.setdefault(current, [])
            if current not in order:
                order.append(current)
            continue
        launch_match = _LAUNCH_LINE.match(stripped)
        if launch_match:
            launch = launch_match.group(1).strip()
            current = ""
            continue
        if current:
            files[current].append(line)
    cleaned: dict[str, str] = {}
    for name in order:
        body = _strip_fence("\n".join(files[name]))
        if body:
            cleaned[name] = body + "\n"
    return cleaned, launch or "python test_main.py"


def format_deliverable(files: dict[str, str], launch: str, result: dict) -> str:
    passed = bool(result.get("passed"))
    lines = [
        "# Software deliverable",
        f"Tests: {'PASSED' if passed else 'FAILED'}",
        f"Sandbox: {result.get('sandbox') or 'not-run'}",
        f"Launch: {launch or 'python test_main.py'}",
        "",
    ]
    for name, source in files.items():
        lines.append(f"## {name}")
        lines.append(source.rstrip())
        lines.append("")
    if not passed:
        detail = (result.get("stderr") or result.get("stdout") or "The tests did not pass.").strip()
        lines += ["Test output:", detail[:1500], ""]
    lines.append("Nothing was sent.")
    return "\n".join(lines).strip() + "\n"


def _prompt(objective: str, failure: str = "") -> str:
    text = (
        "Write the program for this request. Output only this shape, with real Python, and then stop.\n"
        "FILE: main.py\n"
        "FILE: test_main.py\n"
        "LAUNCH: python test_main.py\n"
        "test_main.py imports main and uses assert. The assert must fail if the program is wrong. "
        "Do not use pytest. Do not use the network. Do not write assert True.\n\n"
        f"Request:\n{(objective or '')[:800]}\n"
    )
    if failure:
        text += "\nThe previous files failed.\n" + failure[:1200] + "\nWrite the FILE blocks again.\n"
    return text


def build_deliverable(objective: str, complete) -> tuple[str, dict]:
    """Ask the model for files, run the test, and retry once with the failure."""
    from .code_sandbox import run_files

    text, tokens, meta = complete("Write the files only.", _prompt(objective), 1400)
    files, launch = parse_program(text or "")
    result = run_files(files) if files else {
        "passed": False,
        "stderr": "The model did not write FILE blocks.",
        "stdout": "",
        "sandbox": "not-run",
        "exit_code": None,
        "status": "error",
    }
    if not result.get("passed"):
        text2, tokens2, meta2 = complete(
            "Write the files only.",
            _prompt(objective, result.get("stderr") or result.get("stdout") or ""),
            1400,
        )
        files2, launch2 = parse_program(text2 or "")
        if files2:
            result = run_files(files2)
            text, files, launch, meta = text2, files2, launch2, meta2
            tokens = int(tokens or 0) + int(tokens2 or 0)
    meta = dict(meta or {})
    meta["completion_tokens"] = tokens
    meta["tests_passed"] = bool(result.get("passed"))
    meta["sandbox"] = result.get("sandbox")
    return format_deliverable(files, launch, result), meta
