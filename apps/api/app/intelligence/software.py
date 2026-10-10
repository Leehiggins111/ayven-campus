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
    raw = text or ""
    if re.search(r"^FILE:\s*test_main\.py\s*$", raw, re.I | re.M) and not re.search(r"^FILE:\s*main\.py\s*$", raw, re.I | re.M):
        raw = "FILE: main.py\n" + raw
    files: dict[str, list[str]] = {}
    order: list[str] = []
    current = ""
    launch = ""
    for line in raw.splitlines():
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
    passed = bool(result.get("passed")) and bool(files.get("main.py")) and bool(files.get("test_main.py"))
    lines = [
        "# Software deliverable",
        f"Tests: {'PASSED' if passed else 'FAILED'}",
        f"Sandbox: {result.get('sandbox') or 'not-run'}",
        f"Launch: {launch or 'python test_main.py'}",
        "",
    ]
    if passed:
        for name, source in files.items():
            lines.append(f"## {name}")
            lines.append(source.rstrip())
            lines.append("")
        lines += ["Test output:", "passed", ""]
    else:
        detail = (result.get("stderr") or result.get("stdout") or "The tests did not pass.").strip()
        lines += ["Rejected: " + (detail.splitlines()[0] if detail else "the program was not saved."), "No file was saved.", ""]
    lines.append("Nothing was sent.")
    return "\n".join(lines).strip() + "\n"


def stored_files(findings: str) -> dict[str, str]:
    """Files from a deliverable whose tests passed. A rejection stores none."""
    if "tests: passed" not in (findings or "").lower():
        return {}
    files: dict[str, list[str]] = {}
    current = ""
    for line in (findings or "").splitlines():
        if line.startswith("## ") and line[3:].strip() in {"main.py", "test_main.py"}:
            current = line[3:].strip()
            files[current] = []
            continue
        if current and (line.startswith("## ") or line == "Nothing was sent."):
            current = ""
            continue
        if current:
            files[current].append(line)
    saved = {}
    for name, body in files.items():
        source = "\n".join(body).strip()
        if source:
            saved[name] = source + "\n"
    if "main.py" not in saved or "test_main.py" not in saved:
        return {}
    return saved


def _prompt(objective: str, retry: bool = False) -> str:
    text = (
        "Write the program for this request. Output only this shape, with real Python, and then stop.\n"
        "FILE: main.py\n"
        "FILE: test_main.py\n"
        "LAUNCH: python test_main.py\n"
        "test_main.py imports main and uses assert. The assert must fail if the program is wrong. "
        "Do not use pytest. Do not use the network. Do not write assert True.\n\n"
        f"Request:\n{(objective or '')[:800]}\n"
    )
    if retry:
        text += "\nThe previous answer was not Python. Output only the FILE blocks.\n"
    return text


def _raw_blocks(text: str) -> dict[str, str]:
    files: dict[str, list[str]] = {}
    current = ""
    for line in (text or "").splitlines():
        match = _FILE_LINE.match(line.strip())
        if match:
            current = match.group(1)
            files.setdefault(current, [])
            continue
        if _LAUNCH_LINE.match(line.strip()):
            current = ""
            continue
        if current:
            files[current].append(line)
    return {name: "\n".join(body).strip() for name, body in files.items() if "\n".join(body).strip()}


def _ambiguous_program(text: str) -> bool:
    """Several sketches are not one program. Named file blocks are decided separately."""
    lowered = (text or "").lower()
    if "option 1" in lowered or "possibility a" in lowered or "another idea" in lowered:
        return True
    prints = re.findall(r"^\s*print\s*\(.+\)\s*$", text or "", re.M)
    defs = re.findall(r"^\s*def\s+", text or "", re.M)
    return bool(prints) and bool(defs)


def classify_program(text: str) -> tuple[dict[str, str], str, str]:
    """One syntax-checked program, or a reason nothing was saved."""
    raw_blocks = _raw_blocks(text or "")
    named = "main.py" in raw_blocks and "test_main.py" in raw_blocks
    if not named and _ambiguous_program(text or ""):
        return {}, "python test_main.py", "The reply describes more than one program, so none was saved."
    files, launch = parse_program(text or "")
    if files.get("main.py") and files.get("test_main.py"):
        from .code_sandbox import _tests_are_meaningful

        if not _tests_are_meaningful(files["test_main.py"]):
            return {}, launch, "The test cannot fail, so the program was not saved."
        return files, launch, ""
    if named:
        return {}, "python test_main.py", "The file blocks failed the syntax check, so nothing was saved."
    return {}, "python test_main.py", "The reply did not contain one main.py and one test_main.py, so nothing was saved."


def _invoke(complete, system: str, user: str, limit: int, prefill: str):
    try:
        return complete(system, user, limit, prefill)
    except TypeError:
        return complete(system, user, limit)


def build_deliverable(objective: str, complete) -> tuple[str, dict]:
    """Ask the model for files, run the test, and retry once without pasting the note back."""
    from .code_sandbox import run_files

    text, tokens, meta = _invoke(complete, "Write the files only.", _prompt(objective), 1400, "FILE: main.py\n")
    files, launch, rejection = classify_program(text or "")
    result = run_files(files) if files.get("main.py") and files.get("test_main.py") else {
        "passed": False,
        "stderr": rejection or "The reply did not contain one main.py and one test_main.py, so nothing was saved.",
        "stdout": "",
        "sandbox": "not-run",
        "exit_code": None,
        "status": "error",
    }
    if not result.get("passed"):
        text2, tokens2, meta2 = _invoke(
            complete,
            "Write the files only.",
            _prompt(objective, retry=True) + "\nCheck: " + (rejection or result.get("stderr") or "the tests failed.")[:300] + "\n",
            1400,
            "FILE: main.py\n",
        )
        files2, launch2, rejection2 = classify_program(text2 or "")
        if files2:
            result = run_files(files2)
            if result.get("passed"):
                text, files, launch, meta = text2, files2, launch2, meta2
            else:
                files = {}
                result["stderr"] = result.get("stderr") or "The tests failed, so the files were not saved."
                meta = meta2
        else:
            files = {}
            result = {
                "passed": False,
                "stderr": rejection2,
                "stdout": "",
                "sandbox": "not-run",
                "exit_code": None,
                "status": "error",
            }
            meta = meta2
        tokens = int(tokens or 0) + int(tokens2 or 0)
    meta = dict(meta or {})
    meta["completion_tokens"] = tokens
    meta["tests_passed"] = bool(result.get("passed"))
    meta["sandbox"] = result.get("sandbox")
    return format_deliverable(files, launch, result), meta
