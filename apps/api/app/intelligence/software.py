"""A model-written program. The files are stored only when their tests pass."""

from __future__ import annotations

import re

_FILE_LINE = re.compile(r"^FILE:\s*([A-Za-z0-9_.-]+)\s*$", re.I)
_LAUNCH_LINE = re.compile(r"^LAUNCH:\s*(.+?)\s*$", re.I)
_FENCE = re.compile(r"^```")

PROGRAM_FORMAT = {
    "type": "object",
    "properties": {
        "main.py": {"type": "string"},
        "test_main.py": {"type": "string"},
    },
    "required": ["main.py", "test_main.py"],
}

SOFTWARE_STOPS = (
    "We are to",
    "Option 1",
    "Possibility A",
    "The problem says",
    "Let me ",
)


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

    # Valid source is already the deliverable. Filtering individual lines loses
    # multiline literals, expressions and user-visible strings.
    candidate = textwrap.dedent(source or "").strip()
    if candidate:
        try:
            compile(candidate + "\n", "<ayven>", "exec")
        except SyntaxError:
            pass
        else:
            return candidate + "\n"

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
    as_json = _from_json(text or "")
    if as_json:
        main = _code_only(as_json["main.py"])
        test = _code_only(as_json["test_main.py"])
        if main and test:
            return {"main.py": main, "test_main.py": test}, launch or "python test_main.py"
    return {}, launch or "python test_main.py"


def _from_json(text: str) -> dict[str, str]:
    """A JSON object with the two file bodies. An essay inside the strings is still checked later."""
    import json

    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    main = data.get("main.py")
    test = data.get("test_main.py")
    if not isinstance(main, str) or not isinstance(test, str):
        return {}
    if not main.strip() or not test.strip():
        return {}
    return {"main.py": main, "test_main.py": test}


def _failure_line(detail: str) -> str:
    """The exception, not the traceback header. The header is not the reason."""
    lines = [line.strip() for line in (detail or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("Traceback") or line.startswith("File ") or line.startswith("^"):
            continue
        return line[:300]
    return (lines[-1] if lines else "the program was not saved.")[:300]


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
        lines += ["Rejected: " + _failure_line(detail), "No file was saved.", ""]
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
        if current and (line.startswith("## ") or line in {"Nothing was sent.", "Test output:"}):
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


def execution_evidence(files: dict[str, str], result: dict) -> dict:
    """Bind the actual runner result to the exact source bytes delivered."""
    import hashlib

    return {
        "passed": result.get("passed") is True,
        "exit_code": result.get("exit_code"),
        "status": result.get("status"),
        "sandbox": result.get("sandbox"),
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or "",
        "sha256": {name: hashlib.sha256(body.encode("utf-8")).hexdigest() for name, body in files.items()},
    }


def verified_software(findings: str, research: dict) -> bool:
    """A status sentence cannot substitute for an executed file set."""
    evidence = research.get("software_execution") or {}
    if not isinstance(evidence, dict):
        return False
    if evidence.get("passed") is not True or evidence.get("exit_code") != 0 or evidence.get("status") != "ok":
        return False
    if evidence.get("sandbox") not in {"unshare-user-net-pid", "bubblewrap-unshare-net", "timeout-subprocess-tempdir"}:
        return False
    files = stored_files(findings)
    if not files:
        return False
    return execution_evidence(files, evidence)["sha256"] == evidence.get("sha256")


SPEC_FORMAT = {
    "type": "object",
    "properties": {
        "function": {"type": "string"},
        "parameters": {"type": "array", "items": {"type": "string"}},
        "example_input": {"type": "array"},
        "example_output": {},
    },
    "required": ["function", "parameters", "example_input", "example_output"],
}

TEST_FORMAT = {
    "type": "object",
    "properties": {"test_main.py": {"type": "string"}},
    "required": ["test_main.py"],
}

IMPL_FORMAT = {
    "type": "object",
    "properties": {"main.py": {"type": "string"}},
    "required": ["main.py"],
}


def _requested_sum(objective: str):
    """The two numbers and their sum, when the request is that addition. Otherwise nothing."""
    if not re.search(r"\b(?:add|adds|adding|sum|plus)\b", objective or "", re.I):
        return None
    match = re.search(r"\b(\d+)\s*(?:\+|and|plus)\s*(\d+)\b", objective or "", re.I)
    if not match:
        return None
    left, right = int(match.group(1)), int(match.group(2))
    return left, right, left + right


def parse_spec(text: str) -> dict:
    """A function name, its parameters, one input, and the output that input must produce."""
    import json

    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    data = {}
    if start >= 0 and end > start:
        try:
            loaded = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            data = loaded
    function = str(data.get("function") or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function):
        return {}
    params = data.get("parameters") if isinstance(data.get("parameters"), list) else data.get("args")
    if isinstance(params, str):
        params = [part.strip() for part in params.split(",") if part.strip()]
    if not isinstance(params, list):
        return {}
    names = [str(item).strip() for item in params if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item).strip())]
    example_input = data.get("example_input")
    if not names or not isinstance(example_input, list) or not example_input or "example_output" not in data:
        return {}
    return {
        "function": function,
        "parameters": names,
        "example_input": example_input,
        "example_output": data.get("example_output"),
    }


def _spec_problem(spec: dict, objective: str) -> str:
    wanted = _requested_sum(objective)
    if not wanted:
        return ""
    left, right, total = wanted
    try:
        inputs = [int(item) for item in spec.get("example_input") or []]
    except (TypeError, ValueError):
        return "The example input does not use the numbers in the request."
    if sorted(inputs) != sorted((left, right)):
        return "The example input does not use the numbers in the request."
    try:
        output = int(spec.get("example_output"))
    except (TypeError, ValueError):
        return "The example output is not the result of the operation on the numbers in the request."
    if output != total:
        return "The example output is not the result of the operation on the numbers in the request."
    return ""


def _spec_prompt(objective: str, feedback: str) -> str:
    text = (
        "Read the request. Reply with one JSON object and then stop.\n"
        "Fields: function (a Python name), parameters (a list of names), "
        "example_input (a list of the values from the request), "
        "example_output (the value that function returns for that input).\n"
        "Do not write the program. Do not write a test.\n"
        "A different request would look like "
        '{"function":"double","parameters":["value"],"example_input":[4],"example_output":8}. '
        "Do not copy that example.\n\n"
        f"Request:\n{(objective or '')[:800]}\n"
    )
    if feedback:
        text += f"\nCheck: {feedback}\n"
    return text


def _test_prompt(objective: str, spec: dict, feedback: str) -> str:
    import json

    shown = json.dumps({key: spec[key] for key in ("function", "parameters", "example_input", "example_output")})
    text = (
        "Write the test file only. Reply with one JSON object {\"test_main.py\": \"...\"} and then stop.\n"
        "test_main.py imports main and uses assert. Compare the function's return value with the example output. "
        "Do not use pytest. Do not use the network. Do not write assert True. Do not assert __file__ or __dict__.\n"
        "A different request would look like:\nimport main\nassert main.double(4) == 8\n"
        "Do not copy that example.\n\n"
        f"Spec:\n{shown}\n\nRequest:\n{(objective or '')[:400]}\n"
    )
    if feedback:
        text += f"\nCheck: {feedback}\n"
    return text


def _impl_prompt(objective: str, spec: dict, test_source: str, feedback: str) -> str:
    signature = spec.get("function") or "main"
    params = ", ".join(spec.get("parameters") or [])
    text = (
        "Write main.py only. Reply with one JSON object {\"main.py\": \"...\"} and then stop.\n"
        f"Define {signature}({params}) so the test below passes. Do not write the test again.\n\n"
        f"Test that will be run:\n{(test_source or '')[:1200]}\n\n"
        f"Request:\n{(objective or '')[:400]}\n"
    )
    if feedback:
        text += f"\nCheck: {feedback}\n"
    return text


def _json_field(text: str, field: str) -> str:
    import json

    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return ""
    value = data.get(field) if isinstance(data, dict) else ""
    return value if isinstance(value, str) else ""


def _parse_one_file(text: str, name: str) -> str:
    body = _json_field(text, name)
    if not body:
        blocks = _raw_blocks(text or "")
        body = blocks.get(name) or ""
    if not body and name == "test_main.py" and "assert " in (text or ""):
        loose = _from_loose_code("def _placeholder():\n    return None\n" + (text or ""))
        body = loose.get("test_main.py") or ""
    if not body and name == "main.py" and (_code_line((text or "").strip()) or "def " in (text or "") or "print(" in (text or "")):
        body = _code_only(text or "")
    if not body:
        return ""
    return _code_only(body) or ""


def _assert_problem(test_source: str, objective: str) -> str:
    from .code_sandbox import _tests_are_meaningful

    if not _tests_are_meaningful(test_source):
        if re.search(r"__file__|__dict__", test_source or ""):
            return "The test checked __file__ or __dict__ and cannot fail, so it was not saved. Assert the number the program produces."
        return "The test cannot fail, so the program was not saved."
    asserts = re.findall(r"^\s*assert\s+(.+)$", test_source or "", re.M)
    if not any(re.search(r"==\s*-?\d+(?:\.\d+)?|==\s*['\"]", item) for item in asserts):
        return "The test does not assert an input against an expected output."
    wanted = _requested_sum(objective)
    if wanted:
        _left, _right, total = wanted
        literals = []
        for item in asserts:
            literals.extend(
                int(float(number))
                for number in re.findall(r"==\s*['\"]?(-?\d+(?:\.\d+)?)['\"]?", item)
            )
        if total not in literals:
            return "The assert does not expect the result of the numbers in the request."
    return ""


def _prompt(objective: str, retry: bool = False) -> str:
    return _spec_prompt(objective, "State one function signature and one example." if retry else "")


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


def _looks_like_program_json(text: str) -> bool:
    raw = (text or "").strip()
    return raw.startswith("{") and '"main.py"' in raw and '"test_main.py"' in raw


def classify_program(text: str) -> tuple[dict[str, str], str, str]:
    """One syntax-checked program, or a reason nothing was saved."""
    raw_blocks = _raw_blocks(text or "")
    named = "main.py" in raw_blocks and "test_main.py" in raw_blocks
    if not named and _ambiguous_program(text or "") and not _looks_like_program_json(text or ""):
        return {}, "python test_main.py", "The reply describes more than one program, so none was saved."
    files, launch = parse_program(text or "")
    if files.get("main.py") and files.get("test_main.py"):
        from .code_sandbox import _tests_are_meaningful

        if not _tests_are_meaningful(files["test_main.py"]):
            if re.search(r"__file__|__dict__", files["test_main.py"]):
                return {}, launch, "The test checked __file__ or __dict__ and cannot fail, so it was not saved. Assert the number the program produces."
            return {}, launch, "The test cannot fail, so the program was not saved."
        return files, launch, ""
    if not named and _ambiguous_program(text or ""):
        return {}, "python test_main.py", "The reply describes more than one program, so none was saved."
    if named or _from_json(text or ""):
        return {}, "python test_main.py", "The file blocks failed the syntax check, so nothing was saved."
    return {}, "python test_main.py", "The reply did not contain one main.py and one test_main.py, so nothing was saved."


def _invoke(complete, system: str, user: str, limit: int, prefill: str = "", ollama_format=None):
    try:
        return complete(system, user, limit, prefill, ollama_format=ollama_format)
    except TypeError:
        try:
            return complete(system, user, limit, prefill)
        except TypeError:
            return complete(system, user, limit)


def _rejected(reason: str, meta: dict, tokens: int) -> tuple[str, dict]:
    result = {
        "passed": False,
        "stderr": reason,
        "stdout": "",
        "sandbox": "not-run",
        "exit_code": None,
        "status": "error",
    }
    meta = dict(meta or {})
    meta["completion_tokens"] = tokens
    meta["tests_passed"] = False
    meta["sandbox"] = "not-run"
    return format_deliverable({}, "python test_main.py", result), meta


def build_deliverable(objective: str, complete) -> tuple[str, dict]:
    """Spec, then a test that fails on a stub, then the program. Files are saved only when both hold."""
    from .code_sandbox import run_files, tests_fail_against_stub

    tokens, meta = 0, {}
    feedback = ""
    spec: dict = {}
    ambiguous = ""
    for _attempt in range(2):
        piece, piece_tokens, piece_meta = _invoke(
            complete, "Write the specification only.", _spec_prompt(objective, feedback), 320, "", SPEC_FORMAT,
        )
        tokens += int(piece_tokens or 0)
        meta = piece_meta or {}
        if _ambiguous_program(piece or "") and not parse_spec(piece or ""):
            ambiguous = "The reply describes more than one program, so none was saved."
            feedback = ambiguous
            continue
        found = parse_spec(piece or "")
        problem = _spec_problem(found, objective) if found else "The reply did not state one function signature and an example, so nothing was saved."
        if found and not problem:
            spec = found
            break
        feedback = problem
        spec = {}
    if not spec:
        return _rejected(feedback or ambiguous or "The reply did not state one function signature and an example, so nothing was saved.", meta, tokens)

    feedback = ""
    test_source = ""
    for _attempt in range(2):
        piece, piece_tokens, piece_meta = _invoke(
            complete, "Write the test only.", _test_prompt(objective, spec, feedback), 640, "", TEST_FORMAT,
        )
        tokens += int(piece_tokens or 0)
        meta = piece_meta or {}
        candidate = _parse_one_file(piece or "", "test_main.py")
        if candidate:
            candidate = _qualify_tests(f"def {spec['function']}():\n    pass\n", candidate)
        problem = _assert_problem(candidate, objective) if candidate else "The reply did not contain one test_main.py, so nothing was saved."
        if problem:
            feedback = problem
            test_source = ""
            continue
        mutated = tests_fail_against_stub(candidate, spec.get("function") or "")
        if mutated.get("status") == "disabled":
            feedback = "The tests could not be run."
            test_source = ""
            continue
        if mutated.get("passed"):
            feedback = "The test passed against a function that returns nothing. Assert the input against the expected output."
            test_source = ""
            continue
        if mutated.get("exit_code") != 1 or "AssertionError" not in (mutated.get("stderr") or ""):
            feedback = "The test did not reach a behavioural assertion: " + _failure_line(mutated.get("stderr") or "Execution was unavailable.")
            test_source = ""
            continue
        test_source = candidate
        break
    if not test_source:
        return _rejected(feedback or "The test cannot fail, so the program was not saved.", meta, tokens)

    feedback = ""
    files: dict[str, str] = {}
    result: dict = {"passed": False, "stderr": "", "stdout": "", "sandbox": "not-run", "exit_code": None, "status": "error"}
    for _attempt in range(2):
        piece, piece_tokens, piece_meta = _invoke(
            complete, "Write main.py only.", _impl_prompt(objective, spec, test_source, feedback), 640, "", IMPL_FORMAT,
        )
        tokens += int(piece_tokens or 0)
        meta = piece_meta or {}
        main = _parse_one_file(piece or "", "main.py")
        if not main:
            feedback = "The reply did not contain one main.py, so nothing was saved."
            files = {}
            continue
        files = {"main.py": main, "test_main.py": test_source}
        result = run_files(files)
        if result.get("passed"):
            break
        files = {}
        feedback = _failure_line(result.get("stderr") or "The tests failed, so the files were not saved.")
        result["stderr"] = feedback
    meta = dict(meta or {})
    meta["completion_tokens"] = tokens
    meta["tests_passed"] = bool(result.get("passed")) and bool(files.get("main.py"))
    meta["sandbox"] = result.get("sandbox")
    if not meta["tests_passed"]:
        files = {}
    meta["software_execution"] = execution_evidence(files, result)
    return format_deliverable(files, "python test_main.py", result), meta
