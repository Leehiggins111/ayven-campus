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
    assert _approved_research_is_unresolved(row, "Tests: PASSED\nNothing was sent.\n") is False


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
        if "FILE: main.py" in user:
            return program, 40, {"backend": "generator"}
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
