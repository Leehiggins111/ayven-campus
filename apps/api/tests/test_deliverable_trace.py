"""Each hop from the model reply to the screen keeps the plan and the program."""

import uuid

from fastapi.testclient import TestClient

from app.db import connect
from app.intelligence.execution import _section_drop_reason, run_objective
from app.intelligence.workflow import log_transition
from app.main import app
from app.models import set_role_generator

DOOR = (
    "Prepare a UK business launch plan for a service that replaces kitchen doors, worktops and handles. "
    "A full kitchen replacement is the expensive alternative. "
    "Painting and wrapping are other services, not this service."
)

_ESSAY = """FILE: main.py
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


def test_generation_record_keeps_finish_reason_tokens_timing_and_verbatim_text(monkeypatch, caplog):
    import logging

    from app.intelligence.store import save_model_call
    from app.models import ollama_generation

    payload = {
        "response": "The service is replacing kitchen doors, worktops and handles.",
        "thinking": "SECRET_REASONING",
        "done_reason": "stop",
        "prompt_eval_count": 18,
        "eval_count": 22,
        "total_duration": 1_500_000_000,
    }
    record = ollama_generation(payload, 9)
    assert record["raw_response"] == payload["response"]
    assert "SECRET_REASONING" not in record["raw_response"]
    assert record["finish_reason"] == "stop"
    assert record["prompt_tokens"] == 18
    assert record["completion_tokens"] == 22
    assert record["elapsed_s"] == 1.5

    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(url)
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    caplog.set_level(logging.INFO, logger="ayven.generation")
    from app.models import complete_role

    text, tokens, meta = complete_role("EMPLOYEE", "Write the finished lines only.", "State the service.", 80)
    assert "replacing kitchen doors" in text
    assert meta["finish_reason"] == "stop"
    assert meta["prompt_tokens"] == 18
    assert tokens == 22
    assert meta["raw_response"] == payload["response"]
    assert calls and calls[0].endswith("/api/generate")
    assert payload["response"] in caplog.text
    assert "finish_reason=stop" in caplog.text
    package_id = "trace-" + uuid.uuid4().hex[:8]
    save_model_call(package_id, "EMPLOYEE", "business_research", meta, text)
    conn = connect()
    row = dict(conn.execute(
        "SELECT response_text, finish_reason, prompt_tokens, completion_tokens, latency_s FROM model_calls WHERE package_id=?",
        (package_id,),
    ).fetchone())
    conn.close()
    assert row["response_text"] == payload["response"]
    assert row["finish_reason"] == "stop"
    assert row["prompt_tokens"] == 18
    assert row["completion_tokens"] == 22
    assert row["latency_s"] == 1.5


def test_schema_rejection_keeps_the_reply():
    from pydantic import BaseModel

    from app.intelligence.constrained import enforce_output

    class Decision(BaseModel):
        decision: str

    text, info = enforce_output(Decision, "The service is replacing kitchen doors, worktops and handles.")
    assert info["postcheck"] == "rejected"
    assert text == "The service is replacing kitchen doors, worktops and handles."


def test_narration_does_not_drop_the_owner_sentence_and_a_monologue_is_marked_dropped():
    from app.intelligence.deliverable import owner_section

    mixed = (
        "Okay, the user wants a sentence. "
        "The service is replacing kitchen doors, worktops and handles rather than the whole kitchen."
    )
    kept = owner_section("service", mixed)
    assert "replacing kitchen doors, worktops and handles" in kept
    assert "Okay" not in kept
    assert _section_drop_reason("service", mixed, "", DOOR) == ""
    monologue = "Okay, the user wants me to write a section of a launch plan."
    assert owner_section("service", monologue) == ""
    assert "narration" in _section_drop_reason("service", monologue, "", DOOR).lower()
    assert "nothing" in _section_drop_reason("service", "", "", DOOR).lower()


def test_software_files_are_saved_shown_and_downloadable(monkeypatch):
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    monkeypatch.setenv("AYVEN_USE_QWEN_AGENT", "0")
    objective = "Write a small Python program that adds 2 and 3 and prints the sum."

    def gen(role, system, user, max_tokens):
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: the files are the deliverable", 3, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 4}
        if role == "SUPERVISOR":
            return "ACCEPT\nNothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 4}
        if "FILE: main.py" in user:
            return _ESSAY, 40, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 11}
        return "Nothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 2}

    set_role_generator(gen)
    try:
        parent = run_objective(_project(objective), objective, "task-" + uuid.uuid4().hex[:8])
    finally:
        set_role_generator(None)

    conn = connect()
    row = dict(conn.execute("SELECT findings, workflow_state FROM work_packages WHERE id=?", (parent,)).fetchone())
    calls = [
        dict(item) for item in conn.execute(
            "SELECT response_text, finish_reason, prompt_tokens, completion_tokens FROM model_calls WHERE package_id IN (SELECT id FROM work_packages WHERE id=? OR parent_id=?)",
            (parent, parent),
        ).fetchall()
    ]
    conn.close()
    assert any("However" in (item["response_text"] or "") for item in calls)
    assert any(item["finish_reason"] == "stop" and item["prompt_tokens"] == 11 for item in calls)
    assert "However" not in row["findings"]
    assert "print(2+3)" in row["findings"]
    assert "tests: passed" in row["findings"].lower()
    assert row["workflow_state"] == "AWAITING_APPROVAL"
    log_transition(parent, "APPROVED", "test approval")
    from app.intelligence.execution import resume_approved_package

    assert resume_approved_package(parent) == "COMPLETED"

    client = TestClient(app)
    downloaded = client.get(f"/work-packages/{parent}/deliverable.md")
    assert downloaded.status_code == 200
    assert "print(2+3)" in downloaded.text
    assert "tests: passed" in downloaded.text.lower()
    assert "attachment" in downloaded.headers.get("content-disposition", "")
    view = client.get("/campus/view", params={"package_id": parent}).json()
    assert "print(2+3)" in (view.get("result") or {}).get("findings", "")
    assert "print(2+3)" in (view.get("result") or {}).get("deliverable", "")
    page = client.get("/r3f/campus-app.jsx")
    assert page.status_code == 200
    assert "download-deliverable" in page.text
    assert "download-main" in page.text
    program = client.get(f"/work-packages/{parent}/files/main.py")
    assert program.status_code == 200
    assert "print(2+3)" in program.text
    assert "attachment" in program.headers.get("content-disposition", "")
    assert client.get(f"/work-packages/{parent}/files/missing.py").status_code == 404


def _door_section(user: str) -> str:
    if "three lines" in user:
        return (
            "Headline: Replacement kitchen doors and worktops\n"
            "Body: The service replaces kitchen doors, worktops and handles.\n"
            "Call to action: Ask for a visit and a written scope."
        )
    if "who pays" in user:
        return "The customer is a household that wants the kitchen doors, worktops and handles replaced."
    if "problem this request" in user:
        return "The problem is that a full kitchen replacement costs more than replacing the doors, worktops and handles."
    if "what is sold" in user:
        return "The offer is replacement of kitchen doors, worktops and handles, which differs from a full kitchen replacement."
    if "two opened pages" in user:
        return "Opened pages state the worktops and handles at https://doors.example/replace and no second price was stated."
    if "Start with Assumption" in user:
        return "Assumption: the opened page did not state a durable price, so the doors, worktops and handles are counted on a visit."
    if "how customers" in user:
        return "Customers are reached through local search and a conversation about replacing kitchen doors, worktops and handles."
    if "asks the customer" in user:
        return "Ask for a visit and a written scope before any booking is made."
    if "first practical" in user:
        return "Next, the owner confirms which kitchens to visit and counts the doors, worktops and handles."
    if "still has to be confirmed" in user:
        return "The plan assumes the request named replacement of kitchen doors, worktops and handles, and a visit still has to see them."
    if "does not settle" in user:
        return "Still open: the towns to cover and the condition of the doors, worktops and handles."
    return "The service replaces kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement."


def test_plan_sections_are_stored_and_the_campus_view_returns_them(monkeypatch):
    from app.intelligence.deliverable import plan_sections_filled

    def search(query, limit=8):
        return [{"title": "Replacement kitchen doors", "url": "https://doors.example/replace", "snippet": "replacement kitchen doors worktops and handles", "rerank_score": 0.8}]

    def fetch(url):
        return {"url": url, "title": "Replacement kitchen doors", "text": "We replace kitchen doors, worktops and handles across the UK.", "error": ""}

    monkeypatch.setattr("app.intelligence.research._live_search", search)
    monkeypatch.setattr("app.intelligence.research._open_live", fetch)
    monkeypatch.setattr("app.intelligence.research.search_provider", lambda query, limit=8: search(query, limit))
    monkeypatch.setattr("app.intelligence.research.fetch_provider", fetch)
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("AYVEN_RESEARCH_MODE", "live")
    monkeypatch.setenv("AYVEN_USE_QWEN_AGENT", "0")

    def gen(role, system, user, max_tokens):
        if role == "MANAGER":
            return "SYNTHESISE\nRationale: publish this request only", 3, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 5}
        if role == "SUPERVISOR":
            return "ACCEPT\nNothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 5}
        if "web search queries" in user.lower():
            return "- replacement kitchen doors worktops handles UK prices\n", 4, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 6}
        if any(phrase in user for phrase in ("Write one", "Write two", "Write exactly", "Output only")):
            if "was rejected" in user:
                return "Okay, the user wants a sentence. " + _door_section(user), 20, {"backend": "generator", "finish_reason": "length", "prompt_tokens": 9}
            return "Okay, the user wants me to write a section of a launch plan.", 12, {"backend": "generator", "finish_reason": "length", "prompt_tokens": 8}
        return "Nothing was sent.", 2, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 2}

    set_role_generator(gen)
    try:
        parent = run_objective(_project(DOOR), DOOR, "task-doors")
    finally:
        set_role_generator(None)

    conn = connect()
    row = dict(conn.execute("SELECT findings FROM work_packages WHERE id=?", (parent,)).fetchone())
    calls = [
        dict(item) for item in conn.execute(
            "SELECT response_text, finish_reason FROM model_calls WHERE package_id IN (SELECT id FROM work_packages WHERE id=? OR parent_id=?)",
            (parent, parent),
        ).fetchall()
    ]
    conn.close()
    assert any("Okay, the user wants" in (item["response_text"] or "") for item in calls)
    assert any(item["finish_reason"] == "length" for item in calls)
    assert "replaces kitchen doors, worktops and handles" in (row["findings"] or "").lower()
    assert plan_sections_filled(row["findings"] or "")
    client = TestClient(app)
    downloaded = client.get(f"/work-packages/{parent}/deliverable.md")
    assert downloaded.status_code == 200
    assert "worktops" in downloaded.text.lower()
    view = client.get("/campus/view", params={"package_id": parent}).json()
    shown = (view.get("result") or {}).get("findings") or ""
    assert "worktops" in shown.lower()
    assert shown == (row["findings"] or "").strip() or "worktops" in ((view.get("result") or {}).get("deliverable") or "").lower()


_DOOR_DRAFT = """Service
Replaces kitchen doors, worktops and handles without full kitchen replacement

Target customer
Homeowners requiring kitchen door and worktop replacement who want to avoid a full kitchen replacement.

Problem
Kitchen doors, worktops or handles are worn, and a full kitchen replacement is the expensive alternative.

Offer and positioning
The offer is replacement of kitchen doors, worktops and handles, which differs from a full kitchen replacement.

Competitor and market research
Opened pages state replacement kitchen doors at £10 to £40 on https://doors.example/replace.

Pricing
ASSUMPTION: £10-£40 per standard door
"""

_ADVERT_24 = """Headline: 24/7 Kitchen Repair Service
Body: We fix kitchen doors, worktops, and handles without needing a full kitchen replacement.
Call to action: Reply "Fix it" to get your kitchen repaired today.
"""

_AMBIGUOUS = """We are to write two files: main.py and test_main.py.
Option 1: main.py does the printing.
            print(2 + 3)
Possibility A: make it a function.
            def main():
                print(2 + 3)
            assert main.main() == 5
"""


def test_a_failed_check_keeps_the_full_draft_and_names_the_reason():
    from app.intelligence.deliverable import plan_sections_filled, retained_draft, section_rejection

    reason = section_rejection("advert", _ADVERT_24, "", DOOR)
    assert "24/7" in reason
    assert "not stored" in reason.lower() or "claim" in reason.lower()
    kept = retained_draft(_DOOR_DRAFT + "\nAdvert\n" + _ADVERT_24, DOOR, "£10 £40", {"advert": reason})
    assert "Validation rejected this draft" in kept
    assert "[advert] " in kept
    assert "24/7" in kept
    assert "Replaces kitchen doors, worktops and handles" in kept
    assert "only what that request" not in kept.lower()
    assert plan_sections_filled(kept) is False
    from types import SimpleNamespace

    from app.intelligence.execution import _protect_draft, _publish_plan

    programme = SimpleNamespace(objective=DOOR, research={"evidence": []}, task_class="business_research")
    stored = _publish_plan(programme, _DOOR_DRAFT + "\nAdvert\n" + _ADVERT_24)
    assert stored.startswith("Validation rejected this draft.")
    assert "Replaces kitchen doors" in stored
    assert _protect_draft(programme, {"focus": "draft", "report": stored}) is True
    blank = "Facts come from the request or from a page that was opened.\nThe service is only what that request and the opened pages say."
    assert _protect_draft(programme, {"focus": "draft", "report": blank}) is False


def test_a_retry_names_the_failed_check_and_does_not_repeat_the_rejected_line():
    from app.intelligence.execution import _section_prompt

    user = _section_prompt("Write exactly three lines and stop.", 2, "The line claims 24/7 availability. That claim is not stored.", "")
    assert "availability" in user
    assert "24/7" not in user
    assert "was rejected" in user
    assert "Kitchen Repair Service" not in user
    assert "Previous answer" not in user


def test_an_ambiguous_program_is_rejected_and_a_clear_one_is_downloadable(monkeypatch):
    from app.intelligence.software import classify_program, format_deliverable, stored_files

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    files, _launch, rejection = classify_program(_AMBIGUOUS)
    assert files == {}
    assert "more than one program" in rejection
    stored = format_deliverable({}, "python test_main.py", {"passed": False, "stderr": rejection, "sandbox": "not-run"})
    assert "No file was saved" in stored
    assert "## main.py" not in stored
    assert stored_files(stored) == {}
    syntax, _launch, syntax_reason = classify_program("FILE: main.py\ndef add(\nFILE: test_main.py\nassert True\n")
    assert syntax == {}
    assert "syntax" in syntax_reason.lower()
    clear = """FILE: main.py
print(2+3)
FILE: test_main.py
import main
assert True
"""
    _files, _launch, weak = classify_program(clear)
    assert _files == {}
    assert "cannot fail" in weak.lower()
    weak_file = """FILE: main.py
print(2+3)
FILE: test_main.py
import main
assert main.__file__ != 'test_main.py'
"""
    _files, _launch, file_reason = classify_program(weak_file)
    assert _files == {}
    assert "cannot fail" in file_reason.lower()
    echoed = "Headline: The previous line was an availability claim and is not stored."
    from app.intelligence.deliverable import section_rejection
    echo_reason = section_rejection("advert", echoed, "", DOOR)
    assert "instruction" in echo_reason.lower()


def test_a_rejected_plan_keeps_the_repaired_sections_and_the_failed_check():
    from app.intelligence.deliverable import plan_sections_filled, retained_draft

    repaired = {
        "pricing": "Assumption: plan around £10 to £40 per door for replacing kitchen doors, worktops and handles.",
        "next steps": "The owner's first practical action is to replace kitchen doors, worktops and handles.",
    }
    one_shot = "Next steps\nReplace doors.\nPricing\nAbout £10.\n"
    reason = "The line claims 24/7 availability. That claim is not stored."
    kept = retained_draft(one_shot + "Advert\n" + _ADVERT_24, DOOR, "£10 to £40", {"advert": reason}, repaired)
    assert "[next steps] passed" in kept
    assert "The owner's first practical action" in kept
    assert "[next steps] The section is too short" not in kept
    assert "[pricing] passed" in kept
    assert "Assumption:" in kept
    assert "[advert] " + reason in kept
    assert "Full draft:" in kept
    assert "> " in kept
    assert plan_sections_filled(kept, "£10 to £40") is False


def test_an_availability_headline_is_retried_without_copying_the_claim(monkeypatch):
    from types import SimpleNamespace

    from app.intelligence.deliverable import _REQUIRED
    from app.intelligence.execution import _fill_plan_sections

    sentence = "The service replaces kitchen doors, worktops and handles without a full kitchen replacement."
    sections = {key: sentence for key in _REQUIRED}
    sections["pricing"] = "Assumption: plan around £10 to £40 for replacing kitchen doors, worktops and handles."
    sections["advert"] = ""
    sections["assumptions"] = sentence
    sections["unresolved"] = "Still open: the towns to visit for kitchen door replacement are not settled."
    bad = (
        "Headline: 24/7 Kitchen Service\n"
        "Body: We replace kitchen doors, worktops and handles without a full kitchen replacement.\n"
        'Call to action: Reply yes to ask about kitchen doors.\n'
    )
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None):
        seen.append(user)
        if "was rejected" in user:
            assert "24/7" not in user
            assert "availability" in user
            return "Replacement of kitchen doors, worktops and handles.", 8, {"backend": "test", "finish_reason": "stop", "raw_response": "Replacement of kitchen doors, worktops and handles."}
        return bad, 12, {"backend": "test", "finish_reason": "stop", "raw_response": bad}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme = SimpleNamespace(objective=DOOR, parent_id="", research={"evidence": []})
    rejected = _fill_plan_sections(programme, sections, "£10 to £40")
    assert rejected == {}
    assert "24/7" not in sections["advert"]
    assert sections["advert"].startswith("Headline: Replacement of kitchen doors")
    assert "Body:" in sections["advert"]
    assert len(seen) == 2


def test_json_files_run_and_an_essay_is_not_saved(monkeypatch):
    import json

    from app.intelligence.software import build_deliverable, classify_program

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    payload = json.dumps({
        "main.py": "def add(left, right):\n    return left + right\n",
        "test_main.py": "import main\nassert main.add(2, 3) == 5\n",
    })
    files, _launch, reason = classify_program(payload)
    assert reason == ""
    assert "def add" in files["main.py"]
    essay_json = json.dumps({
        "main.py": "Option 1: print(2 + 3)\nPossibility A:\ndef add():\n    print(2 + 3)\n",
        "test_main.py": "assert True\n",
    })
    saved, _launch, rejection = classify_program(essay_json)
    assert saved == {}
    assert rejection

    def complete(system, user, limit, prefill=""):
        return _AMBIGUOUS, 20, {"finish_reason": "length", "raw_response": _AMBIGUOUS, "completion_tokens": 20}

    text, meta = build_deliverable("Write a small Python program that adds 2 and 3 and prints the sum.", complete)
    assert "No file was saved" in text
    assert "more than one program" in text
    assert meta["tests_passed"] is False
    assert "## main.py" not in text

    state = {"n": 0}

    def retry(system, user, limit, prefill=""):
        state["n"] += 1
        if state["n"] == 1:
            return _AMBIGUOUS, 20, {"finish_reason": "length", "raw_response": _AMBIGUOUS, "completion_tokens": 20}
        return payload, 30, {"finish_reason": "stop", "raw_response": payload, "completion_tokens": 30}

    ran, ran_meta = build_deliverable("Write a small Python program that adds 2 and 3 and prints the sum.", retry)
    assert "tests: passed" in ran.lower()
    assert "def add" in ran
    assert ran_meta["tests_passed"] is True


def test_a_software_essay_is_not_given_a_larger_budget(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        return Response({
            "response": "We are to write a program that adds 2 and 3 and prints the sum.",
            "done_reason": "length",
            "eval_count": 400,
        })

    monkeypatch.setattr("httpx.post", fake_post)
    from app.intelligence.software import PROGRAM_FORMAT, SOFTWARE_STOPS
    from app.models import _openai_compat

    text, _tokens, info = _openai_compat(
        "http://127.0.0.1:11434/v1",
        "local",
        "qwen3:4b",
        "Write the files only.",
        "Write a small Python program that adds 2 and 3 and prints the sum.",
        640,
        stop=list(SOFTWARE_STOPS),
        expand_unfinished=False,
        ollama_format=PROGRAM_FORMAT,
    )
    assert len(calls) == 1
    assert calls[0]["options"]["num_predict"] == 640
    assert calls[0]["format"]["required"] == ["main.py", "test_main.py"]
    assert "We are to" in calls[0]["options"]["stop"]
    assert "response_format" not in calls[0]
    assert "We are to write a program" in text
    assert info["finish_reason"] == "length"
