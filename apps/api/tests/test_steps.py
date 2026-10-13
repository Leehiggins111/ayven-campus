"""Owned steps stay ordered, allowlisted, and small."""

import time

import pytest

from app.db import connect
from app.goals import list_tasks, split_goal
from app.intelligence.steps import (
    DependencyBlocked,
    StepDenied,
    check_call,
    complete_task,
    current_step,
    run_step,
    scoped_prompt,
    start_task,
)


def _project():
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id, title, objective, status, created_at) VALUES(?,?,?,?,?)",
        ("job-steps", "Steps", "Calculate 6 * 7. Publish nothing.", "running", "2026-10-13T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    split_goal("job-steps", "Calculate 6 * 7. Publish nothing.", ["The product is 42"], ["Publish nothing", "Do not invent a price"])


def test_a_later_owner_cannot_start_and_the_prompt_is_one_step():
    _project()
    first = current_step("job-steps")
    assert first["owner"] == "research"
    prompt = scoped_prompt(first)
    assert "Your only step is Research with evidence" in prompt
    assert "Hard constraint: Publish nothing" in prompt
    assert "Commercial assessment" not in prompt
    commercial = next(task for task in list_tasks("job-steps") if task["owner"] == "commercial")
    with pytest.raises(DependencyBlocked):
        start_task(commercial["id"])
    started = start_task(first["id"])
    assert started["status"] == "in_progress"
    blocked = complete_task(first["id"], "I published the listing.")
    assert blocked["ok"] is False
    done = complete_task(first["id"], "The opened page states the product is 42. Nothing was sent.")
    assert done["ok"] is True
    assert done["next"]["owner"] == "commercial"
    assert done["next"]["status"] == "in_progress"
    sales = next(task for task in list_tasks("job-steps") if task["owner"] == "sales")
    with pytest.raises(DependencyBlocked):
        start_task(sales["id"])


def test_the_running_job_shows_one_step_and_does_not_finish_sales():
    from app.intelligence.execution import Programme, _employee_user, run_objective

    _project()
    programme = Programme("job-steps", "Calculate 6 * 7. Publish nothing.")
    shown = _employee_user(programme, {"id": "child", "focus": "sources", "report": ""})
    assert "Your only step is Research with evidence" in shown
    assert "Commercial assessment" not in shown
    run_objective("job-steps", "Calculate 6 * 7. Publish nothing.")
    tasks = {task["owner"]: task["status"] for task in list_tasks("job-steps")}
    assert tasks["research"] == "done"
    assert tasks["sales"] == "queued"


def test_step_tools_are_allowlisted_and_arguments_are_validated():
    assert check_call("research", "calculator", "9.00 - 2.98")["tool"] == "calculator"
    with pytest.raises(StepDenied):
        check_call("sales", "web_search", "guest records")
    with pytest.raises(StepDenied):
        check_call("research", "calculator", "<think>9.00 - 2.98</think>")
    with pytest.raises(StepDenied):
        check_call("commercial", "web_search", "prices")


def test_a_step_retries_through_the_normal_runner(monkeypatch):
    _project()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("step failed")
        time.sleep(0.01)
        return "The opened page states the product is 42. Nothing was sent."

    monkeypatch.setenv("AYVEN_RETRY_BACKOFF_S", "0")
    task = current_step("job-steps")
    outcome = run_step(task["id"], flaky, max_attempts=3)
    assert outcome["ok"] is True
    assert calls["n"] == 2
    assert len(outcome["attempts"]) == 2
