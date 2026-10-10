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


_CODE_LINE = re.compile(
    r"^(?:def |class |import |from |assert |return |print\s*\(|if |elif |else:|for |while |try:|except|finally:|with |pass$|raise |yield |break$|continue$|global |nonlocal |#|@)"
)
_ASSIGN_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\s*(?:\(|=)")


def _code_line(stripped: str) -> bool:
    return bool(_CODE_LINE.match(stripped) or _ASSIGN_LINE.match(stripped))


def _repair_indent(lines: list[str]) -> str:
    """An indented line after a statement that does not open a block is the same statement."""
    repaired: list[str] = []
    for _ in range(max(1, len(lines))):
        candidate = "\n".join(repaired or lines)
        try:
            compile(candidate + "\n", "<ayven>", "exec")
            return candidate
        except IndentationError as exc:
            source = repaired or lines
            if repaired:
                source = repaired
            else:
                source = list(lines)
                repaired = source
            lineno = (exc.lineno or 1) - 1
            if lineno < 0 or lineno >= len(source):
                return ""
            prev = lineno - 1
            while prev >= 0 and not source[prev].strip():
                prev -= 1
            base = 0
            if prev >= 0:
                base = len(source[prev]) - len(source[prev].lstrip(" "))
            source[lineno] = (" " * base) + source[lineno].lstrip()
            repaired = source
            continue
        except SyntaxError:
            return ""
    text = "\n".join(repaired).strip()
    try:
        compile(text + "\n", "<ayven>", "exec")
    except SyntaxError:
        return ""
    return text


def _code_only(source: str) -> str:
    """Drop the essay around a program and dedent what remains."""
    import textwrap

    kept: list[str] = []
    for line in (source or "").splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if re.search(r"\b(however|the problem|we are|let me|note:|important:|but note|but wait|one more)\b", stripped, re.I):
            continue
        if re.match(r"^(file|main\.py|test_main\.py)\s*:?\s*$", stripped, re.I):
            continue
        if _code_line(stripped):
            previous = next((item.strip() for item in reversed(kept) if item.strip()), "")
            if previous == stripped:
                continue
            kept.append(line)
    text = _repair_indent(kept)
    if not text:
        return ""
    text = textwrap.dedent(text).strip()
    try:
        compile(text + "\n", "<ayven>", "exec")
    except SyntaxError:
        return ""
    return text + "\n"


def _qualify_tests(main_source: str, test_source: str) -> str:
    """A bare assert add(...) has to call main.add once the function lives in main.py."""
    names = re.findall(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", main_source, re.M)
    test = test_source.strip()
    if "import main" not in test:
        test = "import main\n" + test
    for name in names:
        test = re.sub(rf"(?<![\w.]){name}\s*\(", f"main.{name}(", test)
    return test if test.endswith("\n") else test + "\n"


def _from_loose_code(text: str) -> dict[str, str]:
    """A fenced program, or a def plus asserts, is still the two files."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S | re.I)
    code = "\n\n".join(block.strip() for block in blocks if block.strip())
    if not code:
        lines = []
        started = False
        for line in (text or "").splitlines():
            stripped = line.strip()
            if re.match(r"^(def |class |import |from |assert )", stripped) or (started and (line.startswith((" ", "\t")) or not stripped)):
                started = True
                lines.append(line)
                continue
            if started and stripped:
                break
        code = "\n".join(lines).strip()
    if "def " not in code or "assert " not in code:
        return {}
    body: list[str] = []
    tests: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("assert ") or (tests and (line.startswith((" ", "\t")) or not stripped)):
            tests.append(line)
        elif stripped.startswith(("import main", "from main")):
            tests.append(line)
        else:
            body.append(line)
    main = "\n".join(body).strip()
    test = "\n".join(tests).strip()
    if not main or not test or "def " not in main:
        return {}
    return {"main.py": main + "\n", "test_main.py": _qualify_tests(main, test)}


def parse_program(text: str) -> tuple[dict[str, str], str]:
    """FILE blocks, or a fenced program with asserts. Anything else is not a file."""
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
    if "main.py" in cleaned and "test_main.py" in cleaned:
        main = _code_only(cleaned["main.py"])
        test = _code_only(cleaned["test_main.py"])
        if main and test:
            return {"main.py": main, "test_main.py": test}, launch or "python test_main.py"
    loose = _from_loose_code(text or "")
    if loose:
        loose = {name: _code_only(body) for name, body in loose.items()}
        if loose.get("main.py") and loose.get("test_main.py"):
            return loose, launch or "python test_main.py"
    return {}, launch or "python test_main.py"


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
    result = run_files(files) if files.get("main.py") and files.get("test_main.py") else {
        "passed": False,
        "stderr": "The model did not write main.py and test_main.py.\n" + (text or "").strip()[:600],
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
