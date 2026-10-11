"""A program request is built, tested, and completed only when the tests pass."""

import uuid

from app.db import connect
from app.intelligence.code_sandbox import run_code, run_files
from app.intelligence.execution import _approved_research_is_unresolved, resume_approved_package, run_objective
from app.intelligence.planner import build_plan, classify
from app.intelligence.workflow import log_transition
from app.models import set_role_generator

_PROGRAM = """FILE: main.py
def add(left, right):
    return left + right

FILE: test_main.py
import main
assert main.add(2, 3) == 5
assert main.add(0, 0) == 0

LAUNCH: python test_main.py
"""

_BROKEN = """FILE: main.py
def add(left, right):
    return left

FILE: test_main.py
import main
assert main.add(2, 3) == 5

LAUNCH: python test_main.py
"""


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-10-10T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def test_a_program_request_is_not_web_research():
    assert classify("Write a small Python program") == "software_build"
    assert classify("Write a small Python program that adds 2 and 3 and prints the sum.") == "software_build"
    assert classify("Say hello") == "trivial"
    plan = build_plan("Write a small Python program", "software_build", [])
    assert "research" in plan["skipped_stages"]
    assert plan["children"][0]["focus"] == "build"


def test_an_essay_around_a_program_is_reduced_to_code(monkeypatch):
    from app.intelligence.software import parse_program

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    text = """FILE: main.py
print(2+3)

   print(2+3)

 File:

However, the problem says we should print the sum.

FILE: test_main.py
import subprocess
   result = subprocess.run(['python', 'main.py'], capture_output=True, text=True)
   assert result.stdout.strip() == '5'

However, the problem says not to write assert True.
"""
    files, _launch = parse_program(text)
    assert "print(2+3)" in files["main.py"]
    assert "However" not in files["main.py"]
    assert "However" not in files["test_main.py"]
    compile(files["main.py"], "main.py", "exec")
    compile(files["test_main.py"], "test_main.py", "exec")
    ran = run_files(files)
    assert ran["passed"] is True, ran["stderr"]


def test_a_fenced_program_is_split_and_run(monkeypatch):
    from app.intelligence.software import parse_program

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")

    text = (
        "Here is the program:\n```python\n"
        "def add(left, right):\n"
        "    return left + right\n\n"
        "assert add(2, 3) == 5\n"
        "```\n"
    )
    files, _launch = parse_program(text)
    assert "def add" in files["main.py"]
    assert "assert " not in files["main.py"]
    assert "import main" in files["test_main.py"]
    assert "main.add(2, 3)" in files["test_main.py"]
    ran = run_files(files)
    assert ran["passed"] is True


def test_files_run_without_a_namespace_and_general_code_does_not(monkeypatch):
    import app.intelligence.code_sandbox as sandbox

    monkeypatch.setattr(sandbox, "_unshare_works", lambda: False)
    monkeypatch.setattr(sandbox, "_bwrap_works", lambda: False)
    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    blocked = run_code("research-e3", "print('should-not-run')\n", approved=True)
    assert blocked.status == "disabled"
    assert "should-not-run" not in (blocked.extracted_content or "")
    ran = run_files({
        "main.py": "def add(left, right):\n    return left + right\n",
        "test_main.py": "import main\nassert main.add(2, 3) == 5\n",
    })
    assert ran["passed"] is True
    assert ran["sandbox"] == "timeout-subprocess-tempdir"
    trivial = run_files({
        "main.py": "def add(left, right):\n    return 0\n",
        "test_main.py": "assert True\n",
    })
    assert trivial["passed"] is False


def test_approval_completes_only_when_the_tests_passed():
    row = {"task_class": "software_build", "observability_json": "{}", "research_json": ""}
    assert _approved_research_is_unresolved(row, "Tests: FAILED\nNothing was sent.\n") is True
    assert _approved_research_is_unresolved(row, "Tests: PASSED\nNothing was sent.\n") is True


def test_delivered_files_keep_multiline_python_and_execute(monkeypatch):
    from app.intelligence.software import parse_program, format_deliverable, stored_files

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    text = '''FILE: main.py
def message():
    parts = [
        "important: customer note",
        "However this is real content",
    ]
    return " ".join(parts)
FILE: test_main.py
import main
assert main.message() == "important: customer note However this is real content"
'''
    files, launch = parse_program(text)
    result = run_files(files)
    assert result["passed"] is True, result
    downloaded = stored_files(format_deliverable(files, launch, result))
    assert downloaded == files
    assert run_files(downloaded)["passed"] is True


def test_completion_requires_execution_of_the_exact_delivered_files(monkeypatch):
    import json
    from app.intelligence.software import parse_program, format_deliverable, execution_evidence
    from app.intelligence.completion import score_task

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    files, launch = parse_program(_PROGRAM)
    result = run_files(files)
    findings = format_deliverable(files, launch, result)
    evidence = execution_evidence(files, result)
    research = {"software_execution": evidence}
    row = {"task_class": "software_build", "research_json": json.dumps(research)}
    assert score_task("software_build", findings, research)["outcome"] == "PASS"
    assert _approved_research_is_unresolved(row, findings) is False
    changed = findings.replace("return left + right", "return left - right")
    assert _approved_research_is_unresolved(row, changed) is True
    assert score_task("software_build", findings, {})["outcome"] == "FAIL"
    evidence["exit_code"] = 1
    assert score_task("software_build", findings, research)["outcome"] == "FAIL"


def test_a_broken_test_import_is_not_proof_of_a_meaningful_assertion(monkeypatch):
    import json
    from app.intelligence.software import build_deliverable

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    def complete(system, user, limit, *args, **kwargs):
        if "specification" in system:
            return json.dumps({"function": "add", "parameters": ["a", "b"], "example_input": [2, 3], "example_output": 5}), 1, {}
        if "test" in system:
            return json.dumps({"test_main.py": "import missing_dependency\nimport main\nassert main.add(2, 3) == 5\n"}), 1, {}
        raise AssertionError("A broken test must be repaired before implementation")
    findings, meta = build_deliverable("Write a Python program that adds 2 and 3", complete)
    assert meta["tests_passed"] is False
    assert "behavioural assertion" in findings


def _run(monkeypatch, program: str, objective: str) -> str:
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    monkeypatch.setenv("AYVEN_USE_QWEN_AGENT", "0")

    def gen(role, system, user, max_tokens):
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: the files are the deliverable", 3, {"backend": "generator"}
        if role == "SUPERVISOR":
            return "ACCEPT\nNothing was sent.", 2, {"backend": "generator"}
        if "Write the test file only" in user or "Write main.py only" in user:
            return program, 40, {"backend": "generator"}
        if "example_output" in user:
            import json
            spec = json.dumps({"function": "add", "parameters": ["left", "right"], "example_input": [2, 3], "example_output": 5})
            return spec, 8, {"backend": "generator"}
        return "Nothing was sent.", 2, {"backend": "generator"}

    set_role_generator(gen)
    try:
        return run_objective(_project(objective), objective, "task-" + uuid.uuid4().hex[:8])
    finally:
        set_role_generator(None)


def test_a_program_is_stored_only_when_its_tests_pass(monkeypatch):
    objective = "Write a small Python program that adds 2 and 3 and prints the sum."
    parent = _run(monkeypatch, _PROGRAM, objective)
    conn = connect()
    row = dict(conn.execute("SELECT findings, workflow_state, task_class FROM work_packages WHERE id=?", (parent,)).fetchone())
    conn.close()
    assert row["task_class"] == "software_build"
    assert "def add" in row["findings"]
    assert "tests: passed" in row["findings"].lower()
    assert "EXPLICIT INFERENCE" not in row["findings"]
    assert row["workflow_state"] == "AWAITING_APPROVAL"
    log_transition(parent, "APPROVED", "test approval")
    assert resume_approved_package(parent) == "COMPLETED"

    failed = _run(monkeypatch, _BROKEN, objective)
    conn = connect()
    failed_row = dict(conn.execute("SELECT findings, workflow_state FROM work_packages WHERE id=?", (failed,)).fetchone())
    conn.close()
    assert "tests: failed" in failed_row["findings"].lower()
    log_transition(failed, "APPROVED", "test approval")
    assert resume_approved_package(failed) == "UNRESOLVED"
