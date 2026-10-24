"""Each hop from the model reply to the screen keeps the plan and the program."""

import os
import re
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
    from app.models import role_model, uses_qwen_chatml

    if os.environ.get("AYVEN_PROVIDER_FORMAT") == "ollama":
        expected = "/api/chat"
    elif uses_qwen_chatml(role_model("EMPLOYEE")):
        expected = "/api/generate"
    else:
        expected = "/api/chat"
    assert calls and calls[0].endswith(expected), calls
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
        if "Write the test file only" in user:
            return _ESSAY, 40, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 11}
        if "Write main.py only" in user:
            return "print(2+3)\n", 4, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 4}
        if "example_output" in user:
            import json
            spec = json.dumps({"function": "add", "parameters": ["left", "right"], "example_input": [2, 3], "example_output": 5})
            return spec, 8, {"backend": "generator", "finish_reason": "stop", "prompt_tokens": 6}
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
            "Headline: Worn kitchen doors, same room.\n"
            "Body: Households can replace the kitchen doors, worktops and handles they already have without a full kitchen replacement.\n"
            "Call to action: Ask for a visit and a written scope."
        )
    if "Name the customer." in user:
        return "The customer is a household that wants the kitchen doors, worktops and handles replaced."
    if "Name the problem." in user:
        return "The problem is that a full kitchen replacement costs more than replacing the doors, worktops and handles."
    if "Name the offer." in user:
        return (
            "The package replaces the kitchen doors, the worktops, and the handles. "
            "The visit measures the door count and the worktop length. "
            "It differs from a full kitchen replacement."
        )
    if "name one opened page" in user:
        return (
            "Kitchen Door Co offers fitted kitchen doors, worktops and handles. "
            "Refresh Kit offers a kitchen door replacement. "
            "This service differs by replacing the doors, worktops and handles rather than a full replacement."
        )
    if "Do not invent a £ figure" in user:
        return (
            "Assumption: price each door from the measured door count, price the worktop from the measured length and material, "
            "and count the handles. Measure the kitchen size, the door count, the door size, the worktop length, "
            "the worktop material, and the handle count before a price is written."
        )
    if "Name the channel." in user:
        return "Customers are reached through local search in the first week, where the owner posts one note about replacing kitchen doors, worktops and handles."
    if "Ask the reader to get in touch" in user:
        return "Ask for a visit and a written scope before any booking is made."
    if "Name the owner's next action." in user:
        return (
            "In the first week the owner opens two supplier pages and writes down the prices they publish for the doors, worktops and handles. "
            "In the first month the owner visits one kitchen and measures the doors."
        )
    if "Name one assumption." in user:
        return "The plan assumes a visit still has to see the kitchen doors, worktops and handles before any figure is used."
    if "Name one open fact." in user:
        return "Still open: the towns to cover and the condition of the doors, worktops and handles."
    if "one line only" in user:
        return "Headline: Worn kitchen doors, same room."
    return "The service replaces kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement."


def test_plan_sections_are_stored_and_the_campus_view_returns_them(monkeypatch):
    from app.intelligence.deliverable import plan_sections_filled

    def search(query, limit=8):
        return [
            {"title": "Kitchen Door Co", "url": "https://doors.example/replace", "snippet": "replacement kitchen doors worktops and handles", "rerank_score": 0.8},
            {"title": "Refresh Kit", "url": "https://refreshkit.co.uk/offer", "snippet": "kitchen doors worktops and handles", "rerank_score": 0.7},
        ]

    def fetch(url):
        if "refreshkit.co.uk" in url:
            return {"url": url, "title": "Refresh Kit", "text": "Refresh Kit replaces kitchen doors, worktops and handles.", "error": ""}
        return {"url": url, "title": "Kitchen Door Co", "text": "We replace kitchen doors, worktops and handles across the UK.", "error": ""}

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
        "pricing": "Assumption: doors — plan around £10 to £40 per door.\nAssumption: worktops — not stated on an opened page.\nAssumption: handles — not stated on an opened page.\nAssumption: still to measure kitchen size, door count, door size, worktop length, worktop material, and handle count.",
        "next steps": "In the first week the owner opens two supplier pages and writes down the prices they publish. In the first month the owner phones one supplier.",
    }
    one_shot = "Next steps\nReplace doors.\nPricing\nAbout £10.\n"
    reason = "The line claims 24/7 availability. That claim is not stored."
    kept = retained_draft(one_shot + "Advert\n" + _ADVERT_24, DOOR, "£10 to £40 per door", {"advert": reason}, repaired)
    assert "[next steps] passed" in kept
    assert "opens two supplier pages" in kept
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

    sections = {key: "" for key in _REQUIRED}
    sections.update({
        "service": "The service replaces worn kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement.",
        "target customer": "The customer is a household that wants worn kitchen doors, worktops and handles replaced.",
        "problem": "A full kitchen replacement costs more and takes longer when the doors, worktops and handles are the worn parts.",
        "offer and positioning": "The package replaces the kitchen doors, the worktops, and the handles. The visit measures the door count and the worktop length. It differs from a full kitchen replacement.",
        "competitor and market research": "Kitchen Door Co (https://doors.example/replace) states a standard door is £10 to £40 [F1].\nRefresh Kit (refreshkit.co.uk) charges £950, and the page says pay on completion [F2].\nThis service differs by replacing kitchen doors, worktops and handles rather than a full replacement.",
        "pricing": "https://doors.example/replace states kitchen doors are £10 to £40 [F1].\nhttps://refreshkit.co.uk/offer can transform your kitchen in one day for only £950, and the page states pay on completion [F2].\nAssumption: price each door from the opened per door figures, price the worktop from the measured length and material, and count the handles. Measure the kitchen size, the door count, the door size, the worktop length, the worktop material, and the handle count before a price is written.",
        "channels": "Households are reached through local search in the first week, where the owner posts one finished kitchen and asks for a reply.",
        "advert": "",
        "call to action": "Ask for a visit and a written scope before any booking is made.",
        "next steps": "In the first week the owner opens two supplier pages and writes down the prices they publish for doors, worktops and handles. In the first month the owner visits one kitchen and sets the price after measuring.",
        "assumptions": "The plan assumes a visit still has to see the doors, worktops and handles before any figure is used.",
        "unresolved": "Still open: the towns to visit for kitchen door replacement are not settled.",
    })
    bad = (
        "Headline: 24/7 Kitchen Service\n"
        "Body: We replace kitchen doors, worktops and handles without a full kitchen replacement.\n"
        'Call to action: Reply yes to ask about kitchen doors.\n'
    )
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        seen.append(user)
        if "was rejected" in user:
            assert "24/7" not in user
            assert "availability" in user
            return "Replacement of kitchen doors, worktops and handles.", 8, {"backend": "test", "finish_reason": "stop", "raw_response": "Replacement of kitchen doors, worktops and handles."}
        return bad, 12, {"backend": "test", "finish_reason": "stop", "raw_response": bad}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme = SimpleNamespace(objective=DOOR, parent_id="", research={"evidence": [{
        "source_url": "https://doors.example/replace",
        "source_title": "Kitchen Door Co",
        "extracted_content": "Kitchen Door Co states kitchen doors are £10 to £40.",
    }, {
        "source_url": "https://refreshkit.co.uk/offer",
        "source_title": "Refresh Kit",
        "extracted_content": "Refresh Kit can transform your kitchen in one day for only £950, pay on completion.",
    }]})
    blob = (
        "https://doors.example/replace\nKitchen Door Co states kitchen doors are £10 to £40.\n"
        "https://refreshkit.co.uk/offer\nRefresh Kit can transform your kitchen in one day for only £950, pay on completion."
    )
    rejected = _fill_plan_sections(programme, sections, blob)
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

    def complete(system, user, limit, prefill="", ollama_format=None):
        return _AMBIGUOUS, 20, {"finish_reason": "length", "raw_response": _AMBIGUOUS, "completion_tokens": 20}

    text, meta = build_deliverable("Write a small Python program that adds 2 and 3 and prints the sum.", complete)
    assert "No file was saved" in text
    assert "more than one program" in text
    assert meta["tests_passed"] is False
    assert "## main.py" not in text

    spec = json.dumps({"function": "add", "parameters": ["left", "right"], "example_input": [2, 3], "example_output": 5})
    test_file = json.dumps({"test_main.py": "import main\nassert main.add(2, 3) == 5\n"})
    main_file = json.dumps({"main.py": "def add(left, right):\n    return left + right\n"})
    state = {"spec": 0}

    def retry(system, user, limit, prefill="", ollama_format=None):
        if "Write the test file only" in user:
            return test_file, 20, {"finish_reason": "stop", "completion_tokens": 20}
        if "Write main.py only" in user:
            return main_file, 20, {"finish_reason": "stop", "completion_tokens": 20}
        state["spec"] += 1
        if state["spec"] == 1:
            return _AMBIGUOUS, 20, {"finish_reason": "length", "raw_response": _AMBIGUOUS, "completion_tokens": 20}
        return spec, 20, {"finish_reason": "stop", "completion_tokens": 20}

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


def test_a_failed_fetch_url_is_not_treated_as_a_cited_source():
    from app.intelligence.contracts import contract_for, evaluate_contract

    contract = contract_for("business_research", {"stages": ["research"], "deliverable": "plan"})
    report = (
        "Joe's Doors replaces kitchen doors and worktops. https://www.joesdoors.co.uk/\n"
        "Nothing was sent.\n"
        "- Retrieval failure (fetch): Server error for url 'https://almamoyo.com/doors/'\n"
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500\n"
    )
    verdict = evaluate_contract(
        contract,
        report=report,
        research={"evidence": [{"source_url": "https://www.joesdoors.co.uk/"}]},
        claims=[],
    )
    assert verdict["passed"] is True
    assert verdict["prohibited"] == []
    invented = evaluate_contract(
        contract,
        report="See https://invented.example/price\nNothing was sent.",
        research={"evidence": [{"source_url": "https://www.joesdoors.co.uk/"}]},
        claims=[],
    )
    assert invented["passed"] is False
    assert invented["prohibited"]


def test_a_split_call_to_action_stays_with_the_advert():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.execution import _join_advert, _section_missing

    draft = {
        "advert": "Headline: Replace kitchen doors, worktops and handles without full replacement\nBody: Doors.example lists kitchen doors, worktops and handles in an existing kitchen at £10-£40 per door",
        "call to action": "Ask for a visit to see the kitchen doors, worktops and handles.",
    }
    joined = _join_advert(draft)
    blob = "https://doors.example/replace\nKitchen door replacement costs £10-£40 per standard door."
    assert "Call to action:" in joined["advert"]
    assert _section_missing("advert", joined["advert"], blob, DOOR) is False
    assert section_rejection("advert", joined["advert"], blob, DOOR) == ""
    percent = section_rejection("advert", "Headline: 100% Kitchen Door Repair\nBody: The service replaces kitchen doors, worktops and handles.\nCall to action: Ask for a visit.", blob, DOOR)
    assert "percentage" in percent.lower()


def test_a_page_explains_why_it_is_relevant_and_a_service_area_is_not_invented():
    from app.intelligence.deliverable import relevance_reason, section_rejection

    page = "Replacement kitchen doors and worktops, fitted without a full kitchen replacement."
    reason = relevance_reason(DOOR, "Kitchen doors", page, "https://doors.example/replace")
    assert "doors" in reason
    assert "worktops" in reason
    assert relevance_reason(DOOR, "Paint prices", "Cabinet respray only.", "https://paint.example") == ""
    forbidden = DOOR + " Do not invent a service area or a home base."
    invented = section_rejection(
        "next steps",
        "Identify 3 local UK service areas for owner travel and then replace the kitchen doors, worktops and handles.",
        "",
        forbidden,
    )
    assert "service area" in invented.lower()
    gap = section_rejection(
        "unresolved",
        "The service area boundary is unknown because the owner is willing to travel to replace kitchen doors, worktops and handles.",
        "",
        forbidden,
    )
    assert gap == ""


def test_owner_review_rejects_meta_echo_and_a_single_part_price(monkeypatch):
    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    from app.intelligence.code_sandbox import tests_fail_against_stub
    from app.intelligence.deliverable import covered_items, section_rejection

    objective = (
        "Create a business launch plan. Lee answered: The service replaces kitchen doors, worktops and handles, "
        "and it avoids a full kitchen replacement. Painting and wrapping are other services, not this service."
    )
    assert covered_items(objective) == ["doors", "worktops", "handles"]
    meta = section_rejection(
        "advert",
        "Headline: Let's think of a service name that does not imply hours or availability.\n"
        "Body: Households can replace worn kitchen doors, worktops and handles without a full kitchen replacement.\n"
        "Call to action: Ask for a visit and a written scope.",
        "",
        objective,
    )
    assert "self-talk" in meta.lower() or "instruction" in meta.lower()
    customer = section_rejection(
        "target customer",
        "The customer is responsible for the cost of the service as per the request.",
        "",
        objective,
    )
    assert customer
    problem = section_rejection(
        "problem",
        "The request is: Lee answered: The service replaces kitchen doors, worktops and handles, and it avoids a full kitchen replacement.",
        "",
        objective,
    )
    assert "objective" in problem.lower() or "request" in problem.lower()
    cta = section_rejection(
        "call to action",
        "Could you confirm if this is the solution you're looking for?",
        "",
        objective,
    )
    assert cta
    steps = section_rejection(
        "next steps",
        "The owner's first practical action is to replace kitchen doors, worktops and handles.",
        "",
        objective,
    )
    assert "action" in steps.lower()
    price = section_rejection(
        "pricing",
        "Assumption: plan around £10-£40 per door, and no other figure is published.",
        "£10-£40 per door",
        objective,
    )
    assert "worktops" in price.lower() and "handles" in price.lower()
    channel = section_rejection(
        "channels",
        "Advert (with three lines: Headline:, Body:, Call to action:).",
        "",
        objective,
    )
    assert "channel" in channel.lower()
    usable = section_rejection(
        "pricing",
        "Assumption: doors — £10-£40 on the opened page, a planning figure only.\n"
        "Assumption: worktops — not stated on an opened page.\n"
        "Assumption: handles — not stated on an opened page.\n"
        "Assumption: price the doors, the worktops, and the handles from those measurements. "
        "Still to measure kitchen size, door count, door size, worktop length, worktop material, and handle count.",
        "£10-£40",
        objective,
    )
    assert usable == ""
    held = tests_fail_against_stub("import main\nassert main.add(2, 3) == 5\n", "add")
    assert held["passed"] is False
    tautology = tests_fail_against_stub("import main\nassert main.add(2, 3) == main.add(2, 3)\n", "add")
    assert tautology["passed"] is True
    page = "In 2026, kitchen door replacement in the UK costs £10-£40 per standard door."
    copied = section_rejection(
        "service",
        "The bicycle repair service replaces kitchen doors and worktops while avoiding full replacement of handles.",
        page,
        objective,
    )
    assert "example" in copied.lower()
    weak_problem = section_rejection(
        "problem",
        "Replacing kitchen door handles avoids full replacement of damaged worktops.",
        page,
        objective,
    )
    assert "problem" in weak_problem.lower()
    restated = section_rejection(
        "offer and positioning",
        "This kitchen service replaces doors, worktops, and handles to avoid full replacement.",
        page,
        objective,
    )
    assert "differs" in restated.lower()
    essay = section_rejection(
        "competitor and market research",
        "Joe's Doors does custom work but the service is simpler. Best Builders gives price benchmarks but no service details.",
        page,
        objective,
    )
    assert "£" in essay
    denied = section_rejection(
        "pricing",
        "Assumption: doors — replaces — not stated on an opened page.\n"
        "Assumption: worktops — not stated on an opened page.\n"
        "Assumption: handles — not stated on an opened page.",
        page,
        objective,
    )
    assert "doors" in denied.lower() and "not stated" in denied.lower()
    mail = section_rejection(
        "channels",
        "Email notifications replace kitchen doors with full replacement handles to avoid structural damage.",
        page,
        objective,
    )
    assert "channel" in mail.lower()
    instructions = section_rejection(
        "advert",
        'Need three lines starting with Headline:, Body:, Call to action:. From the pages, the headline should be benefit-focused like "Fix kitchen doors without full replacement".',
        page,
        objective,
    )
    assert instructions
    coffee = (
        "Prepare a UK business briefing for a coffee machine rental company. "
        "Cover the service, the customers, the competitors, and pricing."
    )
    person = section_rejection(
        "target customer",
        "The coffee machine rental company's customers include Alex, who needs bicycle repairs.",
        "Rental coffee machines from £10 a week.",
        coffee,
    )
    assert "example" in person.lower()
    copied_price = section_rejection(
        "pricing",
        'The example shows it should be like "Assumption: chains — not stated on an opened page".\n'
        "Assumption: coffee — not stated on an opened page.",
        "Most businesses pay between £19 and £96 per week.",
        coffee,
    )
    assert "example" in copied_price.lower()
    vague = section_rejection(
        "call to action",
        "Your coffee machine rental company customers can take a visit or written scope.",
        "Rental coffee machines from £10 a week.",
        coffee,
    )
    assert "step" in vague.lower()
    from app.intelligence.execution import _section_jobs

    prompts = " ".join(job[2].lower() for job in _section_jobs(objective))
    assert "bicycle" not in prompts
    assert "chains" not in prompts
    assert "alex" not in prompts
    echoed_task = section_rejection(
        "target customer",
        "They need me to name a customer, specify the kind of buyer, and use certain service words: replaces, kitchen, doors.",
        page,
        objective,
    )
    assert echoed_task
    contradictory = section_rejection(
        "problem",
        "Replacing kitchen doors without avoiding full replacement of worktops and handles results in cost and delay.",
        page,
        objective,
    )
    assert contradictory
    sold = section_rejection(
        "offer and positioning",
        "This full kitchen replacement service differs by replacing doors and worktops instead of handles.",
        page,
        objective,
    )
    assert "alternative" in sold.lower() or "differs" in sold.lower()
    full_quote = section_rejection(
        "advert",
        "Headline: Kitchen doors replaced\nBody: The work avoids a full kitchen replacement and keeps the cabinets.\nCall to action: Call us for a full replacement quote today.",
        page,
        objective,
    )
    assert full_quote
    businesses = section_rejection(
        "target customer",
        "UK businesses requiring temporary coffee solutions without long-term commitments.",
        "Rental coffee machines from £10 a week.",
        coffee,
    )
    assert businesses == ""
    copied_check = section_rejection(
        "call to action",
        "1: The call to action does not ask the customer to take a step.",
        page,
        objective,
    )
    assert copied_check
    guide = section_rejection(
        "advert",
        "Headline: 2026 UK Kitchen Door Replacement Cost Guide: £10-£40 per Door\n"
        "Body: The service replaces kitchen doors, worktops and handles at £10-£40 per door.\n"
        "Call to action: Get your custom quote today.",
        page,
        objective,
    )
    assert "title" in guide.lower() or "headline" in guide.lower()
    meta_cta = section_rejection(
        "call to action",
        'ask for a quote" might be tricky because it could imply a quote, but in this context it is for the rental.',
        "Rental coffee machines from £10 a week.",
        coffee,
    )
    assert meta_cta


def test_an_unsupported_percentage_is_rejected():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.grounding import ground_text

    blob = "Kitchen door replacement costs £10-£40 per standard door."
    reason = section_rejection(
        "offer and positioning",
        "The service replaces kitchen doors, worktops and handles at 10-40% of a full kitchen.",
        blob,
        DOOR,
    )
    assert "percentage" in reason.lower()
    grounded = ground_text("The service replaces kitchen doors, worktops and handles at 10-40% of a full kitchen.", blob, "")
    assert "10-40%" not in grounded["text"]


def test_a_runtime_failure_keeps_the_exception_and_a_later_program_can_pass(monkeypatch):
    import json

    monkeypatch.setenv("AYVEN_ALLOW_CODE", "1")
    from app.intelligence.software import build_deliverable, classify_program, format_deliverable

    dodged = json.dumps({
        "main.py": "print(2 + 3)\n",
        "test_main.py": "import main\nassert main.__dict__['print'](2 + 3) == 5\n",
    })
    files, _launch, reason = classify_program(dodged)
    assert files == {}
    assert "cannot fail" in reason.lower()
    stored = format_deliverable({}, "python test_main.py", {
        "passed": False,
        "stderr": "Traceback (most recent call last):\n  File \"test_main.py\", line 2, in <module>\nKeyError: 'print'\n",
        "sandbox": "timeout-subprocess-tempdir",
    })
    assert "KeyError: 'print'" in stored
    assert "Rejected: Traceback" not in stored
    calls = {"n": 0, "checks": []}
    spec = json.dumps({"function": "add", "parameters": ["a", "b"], "example_input": [2, 3], "example_output": 5})
    good_test = json.dumps({"test_main.py": "import main\nassert main.add(2, 3) == 5\n"})
    good_main = json.dumps({"main.py": "def add(a, b):\n    return a + b\nprint(add(2, 3))\n"})

    def complete(system, user, limit, prefill=""):
        calls["n"] += 1
        if "Check:" in user:
            calls["checks"].append(user)
        if "Write the test file only" in user:
            if "cannot fail" not in user:
                return dodged, 10, {"finish_reason": "stop"}
            return good_test, 10, {"finish_reason": "stop"}
        if "Write main.py only" in user:
            return good_main, 10, {"finish_reason": "stop"}
        return spec, 8, {"finish_reason": "stop"}

    text, meta = build_deliverable("Write a small Python program that adds 2 and 3 and prints the sum.", complete)
    assert meta["tests_passed"] is True
    assert "def add" in text
    assert any("cannot fail" in item for item in calls["checks"])
    assert calls["n"] >= 3


def test_an_empty_search_is_followed_by_a_shorter_query():
    from app.intelligence.research import research

    coffee = "Prepare a UK business briefing for a coffee machine rental company. Cover the service, the customers, the competitors, and pricing."
    seen = []

    def search(query, limit=8):
        seen.append(query)
        if "prices" in query:
            return [{"title": "no_results", "url": "", "snippet": query}]
        return [{"title": "Coffee machine rental", "url": "https://rental.example/coffee", "snippet": "coffee machine rental for businesses"}]

    def fetch(url):
        return {
            "url": url,
            "title": "Coffee machine rental",
            "text": "A coffee machine rental company supplies coffee machines to UK customers. Rental prices are listed on this page.",
            "links": [],
        }

    result = research(
        "business_research",
        coffee,
        "pkg-coffee",
        "research-e3",
        max_rounds=1,
        queries=["coffee machine rental company customers UK prices"],
        search_fn=search,
        fetch_fn=fetch,
        reviewer=lambda *_args, **_kwargs: "SUFFICIENT",
        browse_fn=lambda *_args, **_kwargs: None,
    )
    assert len(seen) >= 2
    assert any("prices" not in query for query in seen)
    assert result["evidence"]


def test_lite_search_html_is_parsed_when_the_main_page_has_no_links():
    from app.tools import parse_lite_html, parse_search_html, web_search

    html = '<a class="result__a" href="https://doors.example/replace">Door replacement</a><span class="result__snippet">worktops</span>'
    parsed = parse_search_html(html)
    assert parsed[0]["url"] == "https://doors.example/replace"
    lite = '<a class="result-link" href="https://rental.example/coffee">Coffee rental</a><td class="result-snippet">coffee machine rental</td>'
    assert parse_lite_html(lite)[0]["url"] == "https://rental.example/coffee"
    assert parse_search_html("<html>no results</html>") == []

    class Response:
        def __init__(self, text):
            self.text = text
            self.status_code = 200

        def raise_for_status(self):
            return None

    pages = {"https://html.duckduckgo.com/html/": "<html>anomaly</html>", "https://lite.duckduckgo.com/lite/": lite}

    def fake_post(url, data=None, headers=None, timeout=None, follow_redirects=None):
        return Response(pages[url])

    import app.tools as tools
    original = tools.httpx.post
    tools.httpx.post = fake_post
    try:
        hits = web_search("coffee machine rental")
    finally:
        tools.httpx.post = original
    assert hits[0]["url"] == "https://rental.example/coffee"


# The qwen3:8b Alba draft from model-eval run 38158832596. Headings use a markdown hard break.
_QWEN8B_ALBA_DRAFT = (
    "Service  \n"
    "Replacement of kitchen doors, worktops, and handles without requiring a full kitchen replacement. "
    "This service differs from alternatives like painting or wrapping by offering a complete replacement of the existing components.  \n"
    "\n"
    "Target customer  \n"
    "Homeowners seeking cost-effective kitchen renovations, particularly those who want to refresh their kitchen without the expense of a full kitchen overhaul.  \n"
    "\n"
    "Problem  \n"
    "Kitchen renovations can be costly and disruptive. A full kitchen replacement involves significant expense, time, and effort, which many homeowners wish to avoid.  \n"
    "\n"
    "Offer and positioning  \n"
    "A targeted solution for homeowners looking to update their kitchen with high-quality replacements of doors, worktops, and handles. "
    "The service is positioned as a faster, more affordable alternative to full kitchen replacements.  \n"
    "\n"
    "Competitor and market research  \n"
    "Competitors like Refresh Kitchen (https://refreshkitchen.co.uk/) offer similar services, charging £950 for a full kitchen transformation. "
    "Other providers, such as Joe's Doors and The Kitchen Depot, also provide replacement services, though specific pricing details are not stated on their websites.  \n"
    "\n"
    "Pricing  \n"
    "Assumption: Replacement of kitchen doors starts at £11.80 per square metre (https://kitchenwarehouseltd.com/replacement-kitchen-doors). "
    "Worktop replacements are not stated, but the service is positioned as a cost-effective alternative to full kitchen replacements.  \n"
    "\n"
    "Channels  \n"
    "Online booking through a dedicated website, local directories, and word-of-mouth referrals.  \n"
    "\n"
    "Advert  \n"
    "Headline: Refresh Your Kitchen Without the Hassle  \n"
    "Body: Update your kitchen with professional replacement doors, worktops, and handles. Avoid the cost and disruption of a full kitchen replacement.  \n"
    "Call to action: Contact us today to schedule your free consultation and get a tailored quote.  \n"
    "\n"
    "Call to action  \n"
    "Schedule a free consultation to discuss your kitchen renovation needs and receive a tailored quote.  \n"
    "\n"
    "Next steps  \n"
    "Develop a website with booking functionality, create local directory listings, and begin outreach to potential customers through social media and online platforms.  \n"
    "\n"
    "Assumptions  \n"
    "Assumption: Replacement of kitchen doors starts at £11.80 per square metre (https://kitchenwarehouseltd.com/replacement-kitchen-doors). "
    "Worktop replacements are not stated, but the service is positioned as a cost-effective alternative to full kitchen replacements.  \n"
    "\n"
    "Unresolved  \n"
    "The exact pricing for worktop replacements is not stated on any opened page. The service area and home base are not specified.  \n"
    "\n"
    "Nothing was sent."
)


def test_the_8b_alba_problem_keeps_both_sentences():
    """Run 38158832596 stored a 29-character problem. The draft's two sentences are the section."""
    from app.intelligence.deliverable import owner_section, parse_sections, section_rejection

    sections = parse_sections(_QWEN8B_ALBA_DRAFT)
    problem = sections["problem"]
    assert problem.startswith("Kitchen renovations can be costly and disruptive.")
    assert "significant expense, time, and effort" in problem
    assert "29 characters" not in problem
    kept = owner_section("problem", problem)
    assert "costly and disruptive" in kept
    assert "time, and effort" in kept
    reason = section_rejection("problem", problem, "", DOOR)
    assert reason == ""
    split = parse_sections(
        "Problem  \n"
        "Kitchen renovations can be costly and  \n"
        "disruptive. A full kitchen replacement involves significant expense, time, and effort, which many homeowners wish to avoid.\n"
    )
    assert "time, and effort" in split["problem"]
    assert "costly and disruptive" in split["problem"]


def test_pricing_prompt_is_shown_the_worktop_price_the_gate_counts():
    from types import SimpleNamespace

    from app.intelligence.deliverable import component_price_snippets
    from app.intelligence.execution import _section_evidence

    evidence = [{
        "source_url": "https://refreshkitchen.co.uk/",
        "source_title": "Refresh Kitchen | Replacement kitchen doors and worktops",
        "extracted_content": "PAY ON COMPLETION Refresh Your Kitchen are a Scottish based company who can transform your kitchen in one day for only £950.",
    }, {
        "source_url": "https://kitchenwarehouseltd.com/replacement-kitchen-doors",
        "source_title": "Replacement Kitchen Doors UK | Made to Measure | From £11.80",
        "extracted_content": "Replacement kitchens made EASY!",
    }]
    snippets = dict(component_price_snippets(evidence, DOOR))
    assert "£950" in snippets["worktops"]
    assert "refreshkitchen.co.uk" in snippets["worktops"]
    assert "£11.80" in snippets["doors"] or "£950" in snippets["doors"]
    assert snippets["handles"] == ""
    programme = SimpleNamespace(objective=DOOR, research={"evidence": evidence})
    shown = _section_evidence(programme, "pricing")
    assert "own words" in shown
    assert "Copy the £ figure" not in shown
    assert "£950" in shown and "refreshkitchen.co.uk" in shown
    assert "not stated" in shown
    other = _section_evidence(programme, "problem")
    assert "not stated" not in other
    assert shown.startswith(other)


_REFRESH = (
    "https://refreshkitchen.co.uk/ Refresh Kitchen | Replacement kitchen doors and worktops "
    "PAY ON COMPLETION Refresh Your Kitchen are a Scottish based company who can transform your kitchen in one day for only £950."
)
_HOUSE = (
    "https://kitchenwarehouseltd.com/replacement-kitchen-doors "
    "Replacement Kitchen Doors UK | Made to Measure | From £11.80 Replacement kitchens made EASY!"
)
_MEASURED = (
    "Assumption: price the doors, the worktops, and the handles from those measurements. "
    "Still to measure kitchen size, door count, door size, worktop length, worktop material, and handle count."
)


def test_a_price_unit_the_page_does_not_state_is_rejected():
    """£11.80 on the Kitchenware House title is not a per-square-metre door price."""
    from app.intelligence.deliverable import claim_mismatch, section_rejection

    claim = (
        "Assumption: doors — Replacement of kitchen doors starts at £11.80 per square metre "
        "(https://kitchenwarehouseltd.com/replacement-kitchen-doors)."
    )
    reason = claim_mismatch(claim, _HOUSE)
    assert "per square metre" in reason
    stored = section_rejection(
        "pricing",
        claim + "\nAssumption: worktops — not stated.\nAssumption: handles — not stated.\n" + _MEASURED,
        _HOUSE + "\n" + _REFRESH,
        DOOR,
    )
    assert "per square metre" in stored
    sample = claim_mismatch(
        "Assumption: doors start at £11.80 for a vinyl wrap sample (https://kitchenwarehouseltd.com/replacement-kitchen-doors).",
        _HOUSE,
    )
    assert "vinyl" in sample or "sample" in sample


def test_a_competitor_price_has_to_use_the_pages_own_words():
    """£950 is Refresh Kitchen's one-day offer, not a full kitchen transformation."""
    from app.intelligence.deliverable import claim_mismatch, section_rejection

    invented = (
        "Competitors like Refresh Kitchen (https://refreshkitchen.co.uk/) offer similar services, "
        "charging £950 for a full kitchen transformation."
    )
    reason = claim_mismatch(invented, _REFRESH)
    assert "full" in reason and "transformation" in reason
    assert "square metre" not in reason
    stored = section_rejection("competitor and market research", invented, _REFRESH, DOOR)
    assert "full" in stored and "transformation" in stored
    faithful = (
        "Refresh Kitchen (https://refreshkitchen.co.uk/) can transform your kitchen in one day for only £950, "
        "and the page states pay on completion."
    )
    assert claim_mismatch(faithful, _REFRESH) == ""
    omitted = "Refresh Kitchen (https://refreshkitchen.co.uk/) can transform your kitchen in one day for only £950."
    assert "pay on completion" in claim_mismatch(omitted, _REFRESH)
    # A clause after the product is not the product. The £ and the unit still have to match the named page.
    walks = (
        "https://walks.example/prices\n"
        "Individual walk – From £18."
    )
    commentary = (
        "Walks Example (https://walks.example/prices) charges from £18 for individual walks, "
        "indicating a range of services."
    )
    assert claim_mismatch(commentary, walks) == ""
    wrong_site = (
        "http://thecoffeevendor.co.uk/\n"
        "The Coffee Vendor. Hire a machine for a day or weekend.\n"
        "https://coffeenerds.co.uk/pages/cost\n"
        "Most businesses pay between £19 and £96 per week."
    )
    misnamed = "The Coffee Vendor (http://thecoffeevendor.co.uk/) charges between £19 and £96 per week."
    assert "19" in claim_mismatch(misnamed, wrong_site)
    hire = (
        "https://tomscoffee.co.uk/short-term-coffee-machine-hire/\n"
        "Coffee Machine Hire from £495/Week\n"
        "1 week (minimum) £495 plus vat"
    )
    dropped = (
        "Tom's Coffee (https://tomscoffee.co.uk/short-term-coffee-machine-hire/) "
        "provides short-term rentals starting at £495 per week."
    )
    assert "plus vat" in claim_mismatch(dropped, hire)
    kept = (
        "Tom's Coffee (https://tomscoffee.co.uk/short-term-coffee-machine-hire/) "
        "provides 1 week (minimum) hire at £495 plus vat per week."
    )
    assert claim_mismatch(kept, hire) == ""
    decimal = (
        "https://coffeenerds.co.uk/pages/cost\n"
        "Most businesses pay between £19 and £96 per week. Roughly £10.60 a week."
    )
    rounded = (
        "Coffee Nerds (https://coffeenerds.co.uk/pages/cost) states weekly rates from £10 to £96."
    )
    assert "10" in claim_mismatch(rounded, decimal)
    exact = (
        "Coffee Nerds (https://coffeenerds.co.uk/pages/cost) states most businesses pay between £19 and £96 per week."
    )
    assert claim_mismatch(exact, decimal) == ""
    rental_site = (
        "https://www.coffeemachinerentals.co.uk/\n"
        "prices ranging from £1,000 to £12,000"
    )
    offer = "The offer replaces kitchen doors and differs from painting, with rates starting from £10."
    assert claim_mismatch(offer, rental_site) == ""
    assert "without naming the opened page" in section_rejection(
        "offer and positioning", offer, rental_site + "\nhttps://london.example/rent\nfrom £10 a week", DOOR,
    )
    both = (
        "https://coffeenerds.co.uk/pages/cost\n"
        "Most businesses pay between £19 and £96 per week.\n"
        "https://londoncoffeemachines.co.uk/rental\n"
        "Rental Coffee Machines from £10 a week"
    )
    fair = (
        "Opened pages state £19 to £96 per week on coffeenerds.co.uk "
        "and £10 per week on londoncoffeemachines.co.uk."
    )
    swapped = (
        "Opened pages state £19 to £96 per week on londoncoffeemachines.co.uk "
        "and £10 per week on coffeenerds.co.uk."
    )
    assert claim_mismatch(fair, both) == ""
    assert claim_mismatch(swapped, both) != ""
    titled = (
        "https://refreshkitchen.co.uk/\n"
        "Refresh Kitchen | Replacement kitchen doors and worktops\n"
        "PAY ON COMPLETION transform your kitchen in one day for only £950.\n"
        "https://doorsandworktops.com/\n"
        "Replacement kitchen doors and worktops.\n"
    )
    quoted = (
        'Refresh Kitchen | Replacement kitchen doors and worktops states '
        '"transform your kitchen in one day for only £950".'
    )
    linked = claim_mismatch(quoted, titled)
    assert "which opened page" not in linked
    assert "pay on completion" in linked
    from app.intelligence.deliverable import section_rejection as reject_section, unsupported_promise

    swatches = (
        "Assumption: doors are ordered as colour swatches before you buy.\n"
        "Assumption: worktops are described in a guide.\n"
        "Assumption: handles are not stated.\n"
        + _MEASURED
    )
    assert "does not use" in reject_section("pricing", swatches, titled, DOOR)
    builders = (
        "https://www.bestbuilders.co.uk/guides/costs/kitchen-door-replacement-cost\n"
        "Kitchen Door Replacement Cost UK 2026 | £10-£40 a Door\n"
        "In 2026, kitchen door replacement in the UK costs £10-£40 per standard door.\n"
        "https://www.nickarchibold.com/replacement-kitchen-doors-cost\n"
        "Cost To Replace Kitchen Doors\n"
        "Replacement kitchen doors and drawer fronts from £6,000 to £10,000, including new hinges and handles.\n"
    )
    named = "Best Builders states kitchen door replacement in the UK costs £10-£40 per standard door."
    assert claim_mismatch(named, builders) == ""
    wrong_unit = "Best Builders states kitchen door replacement costs £10-£40 per square metre."
    assert "per square metre" in claim_mismatch(wrong_unit, builders)
    handles = (
        "Assumption: doors cost £10-£40 per standard door.\n"
        "Assumption: worktops — not stated.\n"
        "Assumption: handles cost is not stated.\n"
        + _MEASURED
    )
    assert section_rejection("pricing", handles, builders, DOOR) == ""
    priced_handles = builders + "Handles from £12 each.\n"
    assert "handles" in section_rejection("pricing", handles, priced_handles, DOOR)
    promise = "Contact us today to schedule a free consultation and refresh the kitchen in one day."
    assert "free consultation" in unsupported_promise(promise, DOOR, titled)
    assert unsupported_promise(quoted, DOOR, titled) == ""
    borrowed_figure = "Assumption: doors cost £950 for a full kitchen transformation including doors and worktops."
    assert claim_mismatch(borrowed_figure, titled) != ""
    from app.intelligence.deliverable import page_line_for_check

    shown = page_line_for_check("The opened page states 'pay on completion' with the £ figure. The claim does not.", titled)
    assert "£950" in shown
    assert "pay on completion" in shown.lower()


def test_a_page_price_without_a_spec_is_a_market_reference():
    from app.intelligence.deliverable import measurements_still_needed, section_rejection

    needed = measurements_still_needed(DOOR)
    assert needed == ["kitchen size", "door count", "door size", "worktop length", "worktop material", "handle count"]
    assert measurements_still_needed("Prepare a UK business briefing for a coffee machine rental company.") == []
    bare = (
        "Assumption: doors — Kitchenware House (https://kitchenwarehouseltd.com/replacement-kitchen-doors) states from £11.80.\n"
        "Assumption: worktops — Refresh Kitchen (https://refreshkitchen.co.uk/) can transform your kitchen in one day for only £950, "
        "and the page states pay on completion.\n"
        "Assumption: handles — not stated on an opened page."
    )
    reason = section_rejection("pricing", bare, _HOUSE + "\n" + _REFRESH, DOOR)
    assert "market reference" in reason
    assert "door count" in reason and "worktop length" in reason
    kept = section_rejection("pricing", bare + "\n" + _MEASURED, _HOUSE + "\n" + _REFRESH, DOOR)
    assert kept == ""


def test_section_prompts_share_one_evidence_prefix_and_a_short_reason_has_no_count():
    from types import SimpleNamespace

    from app.intelligence.deliverable import _body_rejection
    from app.intelligence.execution import _employee_user, _section_evidence, _section_jobs, _section_prompt

    assert _body_rejection("Too short.") == "The section is too short to use."
    programme = SimpleNamespace(objective=DOOR, task_class="business_research", research={"evidence": [{
        "source_url": "https://refreshkitchen.co.uk/",
        "source_title": "Refresh Kitchen | Replacement kitchen doors and worktops",
        "extracted_content": _REFRESH.split(" ", 1)[-1],
    }], "gaps": []})
    problem = _section_evidence(programme, "problem")
    pricing = _section_evidence(programme, "pricing")
    assert pricing.startswith(problem)
    instruction = next(job[2] for job in _section_jobs(DOOR) if job[0] == "problem")
    user = _section_prompt(instruction, 1, "", problem)
    assert user.startswith(problem)
    assert user.index("Facts:") < user.index("Name the problem.")
    pricing_job = next(job[2] for job in _section_jobs(DOOR) if job[0] == "pricing")
    assert "door count" in pricing_job and "worktop material" in pricing_job
    assert "alba" not in pricing_job.lower()
    coffee = next(job[2] for job in _section_jobs("Prepare a UK briefing for a coffee machine rental company.") if job[0] == "pricing")
    assert "door count" not in coffee
    long = "MARKER-NOT-A-PRICE " + ("filler " * 500)
    evidence_user = _employee_user(
        SimpleNamespace(
            objective=DOOR,
            task_class="business_research",
            research={"evidence": [{
                "source_url": "https://doors.example/replace",
                "source_title": "Replacement kitchen doors",
                "extracted_content": long,
            }], "gaps": []},
        ),
        {"focus": "evidence", "report": long},
    )
    assert "MARKER-NOT-A-PRICE" not in evidence_user
    assert "https://doors.example/replace" in evidence_user


def test_a_service_name_from_an_opened_page_is_rejected():
    """The business is the request. A competitor title is not the service."""
    from app.intelligence.deliverable import borrowed_service_name, section_rejection

    objective = "Create a plan for a home visit service. The owner is willing to travel."
    blob = (
        "https://cocos.example/prices\n"
        "Prices – Coco's Mobile Studio\n"
        "Individual walk – From £18."
    )
    borrowed = section_rejection(
        "service",
        "The service is Coco's Mobile Studio, and it visits the customer at home.",
        blob,
        objective,
    )
    assert "opened page" in borrowed
    assert "Coco's Mobile Studio" in borrowed
    assert borrowed_service_name("The service visits the customer's home.", objective, blob) == ""
    named = "Create a plan for Northwind Home Visits. The owner is willing to travel."
    page = "https://northwind.example/\nNorthwind Home Visits\nA home visit."
    assert borrowed_service_name("The service is Northwind Home Visits.", named, page) == ""
    generic = "Create a plan for a mobile dog-grooming service in the UK."
    page = "Mobile Dog Grooming Services\nCoco's Mobile Studio"
    assert borrowed_service_name("Mobile Dog Grooming Services visit customers at home.", generic, page) == ""
    assert "Coco's Mobile Studio" in borrowed_service_name("Book Coco's Mobile Studio.", generic, page)


def test_later_sentences_keep_the_customer_the_problem_and_the_offer():
    from app.intelligence.deliverable import owner_section, section_rejection

    customer = "Pet owners in the UK who want the service at home."
    assert section_rejection("target customer", customer, "", "") == ""
    problem = (
        "Many owners struggle to find a convenient option.\n"
        "A shop visit is inconvenient and takes time."
    )
    assert "inconvenient" in owner_section("problem", problem)
    assert section_rejection("problem", problem, "", "") == ""
    offer = (
        "The offer is a home visit.\n"
        "The offer differs from a shop by coming to the home."
    )
    assert "differs" in owner_section("offer and positioning", offer)
    assert section_rejection("offer and positioning", offer, "", "A home visit service.") == ""
    shared = "The owner posts on the website in the first week and phones one organiser."
    assert "shared form" in section_rejection("channels", shared, "", "")
    specific = (
        "In the first week the owner lists the service on Checkatrade and posts before-and-after photos "
        "in a local Facebook group."
    )
    assert section_rejection("channels", specific, "", "") == ""
    generic = "Online platforms, local directories, and word-of-mouth referrals."
    assert section_rejection("channels", generic, "", "")
    assert "24/7" in section_rejection("channels", "Customers are reached through 24/7 online availability.", "", "")


def test_a_channels_availability_claim_is_retried_and_the_plan_can_keep_the_repair(monkeypatch):
    from types import SimpleNamespace

    from app.intelligence.deliverable import _REQUIRED
    from app.intelligence.execution import _fill_plan_sections

    sections = {key: "kept" for key in _REQUIRED}
    sections.update({
        "service": "The service replaces worn kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement.",
        "target customer": "The customer is a household that wants worn kitchen doors, worktops and handles replaced.",
        "problem": "A full kitchen replacement costs more and takes longer when the doors, worktops and handles are the worn parts.",
        "offer and positioning": "The package replaces the kitchen doors, the worktops, and the handles. The visit measures the door count and the worktop length. It differs from a full kitchen replacement.",
        "competitor and market research": "Kitchen Door Co (https://doors.example/replace) states a standard door is £10 to £40 [F1].\nRefresh Kit (refreshkit.co.uk) charges £950, and the page says pay on completion [F2].\nThis service differs by replacing kitchen doors, worktops and handles rather than a full replacement.",
        "pricing": "https://doors.example/replace states kitchen doors are £10 to £40 [F1].\nhttps://refreshkit.co.uk/offer can transform your kitchen in one day for only £950, and the page states pay on completion [F2].\nAssumption: price each door from the opened per door figures, price the worktop from the measured length and material, and count the handles. Measure the kitchen size, the door count, the door size, the worktop length, the worktop material, and the handle count before a price is written.",
        "channels": "",
        "advert": "Headline: Fresh kitchen doors\nBody: The service replaces kitchen doors, worktops and handles and differs from a full replacement.\nCall to action: Ask for a visit.",
        "call to action": "Ask for a visit and a written scope before any booking is made.",
        "next steps": "In the first week the owner opens two supplier pages and writes down the prices they publish for doors, worktops and handles. In the first month the owner visits one kitchen and sets the price after measuring.",
        "assumptions": "The plan assumes a visit still has to see the doors, worktops and handles before any figure is used.",
        "unresolved": "Still open: the towns to visit for kitchen door replacement are not settled.",
    })
    prefills = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        prefills.append(prefill)
        if "was rejected" in user:
            assert "24/7" not in user
            return "local search and posts one note about kitchen doors in the first week.", 8, {"backend": "test", "finish_reason": "stop", "raw_response": "local search and posts one note about kitchen doors in the first week."}
        return "24/7 online availability.", 6, {"backend": "test", "finish_reason": "stop", "raw_response": "24/7 online availability."}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme = SimpleNamespace(objective=DOOR, parent_id="", research={"evidence": [{
        "source_url": "https://doors.example/replace",
        "source_title": "Kitchen Door Co",
        "extracted_content": "Kitchen Door Co states kitchen doors are £10 to £40.",
    }, {
        "source_url": "https://refreshkit.co.uk/offer",
        "source_title": "Refresh Kit",
        "extracted_content": "Refresh Kit can transform your kitchen in one day for only £950, pay on completion.",
    }]})
    blob = (
        "https://doors.example/replace\nKitchen Door Co states kitchen doors are £10 to £40.\n"
        "https://refreshkit.co.uk/offer\nRefresh Kit can transform your kitchen in one day for only £950, pay on completion."
    )
    rejected = _fill_plan_sections(programme, sections, blob)
    assert rejected == {}
    assert "24/7" not in sections["channels"]
    assert "search" in sections["channels"]
    assert prefills[1] == "The owner uses "


def test_a_fact_table_keeps_cited_quotes_and_drops_invented_names():
    from app.intelligence.facts import enforce_citations, extract_facts

    evidence = [{
        "source_url": "https://www.bestbuilders.co.uk/guides/costs/kitchen-door-replacement-cost",
        "source_title": "Kitchen Door Replacement Cost UK 2026",
        "extracted_content": "In 2026, kitchen door replacement in the UK costs £10-£40 per standard door, excluding handles.",
    }, {
        "source_url": "https://www.nickarchibold.com/replacement-kitchen-doors-cost",
        "source_title": "Cost To Replace Kitchen Doors",
        "extracted_content": "Replacement kitchen doors and drawer fronts from £6,000 to £10,000, including new hinges and handles.",
    }]
    facts = extract_facts(evidence)
    assert facts[0]["id"] == "F1"
    assert facts[0]["quantity"]
    assert "per standard door" in facts[0]["unit"]
    assert "excluding handles" in facts[0]["included"]
    assert facts[0]["source"].startswith("https://www.bestbuilders.co.uk/")
    objective = "The service replaces kitchen doors, worktops and handles."
    invented = "London Coffee Hub charges £150 per month, and the service is free and available 24/7."
    assert enforce_citations(invented, facts, objective) == ""
    opinion = "The owner posts on the website and asks the customer to get in touch."
    assert enforce_citations(opinion, facts, objective) == opinion
    cited = "Best Builders states a door price of £400 per square metre [F1]."
    assert enforce_citations(cited, facts, objective) == ""
    kept = "Bestbuilders states kitchen door replacement costs £10 to £40 per standard door, excluding handles [F1]."
    repaired = enforce_citations(kept, facts, objective)
    assert repaired == kept
    assert 'states "' not in repaired
    assumption = "Assumption: the handle price is set after measuring the handle count."
    assert enforce_citations(assumption, facts, objective) == assumption
    promise = "The opened page offers the work in one day [F1]."
    assert enforce_citations(promise, facts, objective) == ""
    headline = "Headline: Refresh Your Kitchen Without A Full Replacement"
    assert enforce_citations(headline, facts, objective) == headline
    counted = "The owner phones 3 suppliers and asks what they charge."
    assert "3" not in enforce_citations(counted, facts, objective)
    assert "phones" in enforce_citations(counted, facts, objective)


def test_a_cited_price_names_the_page_and_a_title_is_not_the_channel():
    from app.intelligence.deliverable import evidence_blob, section_rejection
    from app.intelligence.facts import extract_facts, repair_before_gate

    evidence = [{
        "source_url": "https://www.coffeemachinerentals.co.uk/",
        "source_title": "Coffee Machine Rental, Leasing & Buying",
        "extracted_content": "Commercial coffee machines can be quite expensive, with prices ranging from £1,000 to £12,000 and even higher.",
    }]
    facts = extract_facts(evidence)
    objective = "The service is coffee machine rental for offices. Buying outright is the alternative."
    blob = evidence_blob(evidence)
    competitor = "Commercial machines are priced from £1,000 to £12,000 [F1]."
    repaired = repair_before_gate("competitor and market research", competitor, facts, objective, blob)
    assert "coffeemachinerentals.co.uk" in repaired
    assert "fewer than two" in section_rejection("competitor and market research", repaired, blob, objective)
    pricing = "The price is set after measuring demand."
    priced = repair_before_gate("pricing", pricing, facts, objective, blob)
    assert priced == pricing
    assert "weekly price is set" not in priced.lower()
    assert section_rejection("pricing", priced, blob, objective)
    assert repair_before_gate("pricing", "", facts, objective, blob) == ""
    channels = "Customers are reached through 6wresearch.com, where the owner posts one note."
    reached = repair_before_gate("channels", channels, facts, objective, blob)
    assert "posts on the website in the first week and phones" not in reached
    assert section_rejection("channels", reached, blob, objective)
    advert = (
        "Headline: Expert Kitchen Renovations Without Full Replacement\n"
        "Body: The offer covers coffee machine rental and differs from buying outright.\n"
        "Call to action: Explore the range."
    )
    # The headline is a title, not this request. The call to action does not ask.
    # A page title in the headline is rejected; the repair must not keep it.
    blob_with_title = blob + "\nExpert Kitchen Renovations Without Full Replacement"
    fixed = repair_before_gate("advert", advert, facts, objective, blob_with_title)
    assert "Kitchen Renovations" not in fixed
    assert "Contact us and ask about a visit" not in fixed
    assert section_rejection("advert", fixed, blob_with_title, objective)
    promised = (
        "Headline: Refresh the kitchen you already have\n"
        "Body: The offer covers coffee machine rental and differs from buying outright.\n"
        "Call to action: Contact us today to get a free quote."
    )
    kept = repair_before_gate("advert", promised, facts, objective, blob)
    assert "free quote" not in kept.lower()
    assert "Contact us and ask about a visit" not in kept
    assert "Refresh the kitchen" in kept
    assert section_rejection("advert", kept, blob, objective)


def test_a_related_product_code_is_not_the_price():
    """Jura W8 on a related-products strip is not the rental offer."""
    from app.intelligence.deliverable import evidence_blob, section_rejection
    from app.intelligence.facts import extract_facts, repair_before_gate

    evidence = [{
        "source_url": "https://www.fullhousecoffee.com/2026/05/how-much-does-it-cost-to-rent-a-coffee-machine",
        "source_title": "How much does it cost to rent a coffee machine",
        "extracted_content": "Related Products Starting From £18.00/week Jura W8 - 50 Cups Per Day - 295w x 416h x 444d",
    }, {
        "source_url": "https://www.nationwidecoffee.co.uk/",
        "source_title": "From £2.70 Per Day | Nationwide Coffee",
        "extracted_content": "Commercial coffee machines are available for rent or purchase.",
    }]
    facts = extract_facts(evidence)
    objective = "Prepare a UK business briefing for a coffee machine rental company."
    priced = repair_before_gate("pricing", "The price is set after measuring.", facts, objective, evidence_blob(evidence))
    assert "Jura" not in priced
    assert "weekly price is set" not in priced.lower()
    assert section_rejection("pricing", priced, evidence_blob(evidence), objective)


def test_a_title_scrap_ending_in_and_is_not_the_competitor():
    from app.intelligence.facts import _proper_name

    assert _proper_name("Single Bean and") is False
    assert _proper_name("Hale and Murray") is True


def test_a_catalogue_code_is_not_the_offer():
    """Starting from £31/week BESTIR BM50 is a price. The model code is not the offer."""
    from app.intelligence.facts import extract_facts

    facts = extract_facts([{
        "source_url": "https://www.fullhousecoffee.com/",
        "source_title": "Office Coffee Machine Rental UK | Full House Coffee",
        "extracted_content": (
            "Starting From £31.00/week BESTIR BM50 - 100 Cups Per Day - Fresh Milk - "
            "304w x 572h x 559d Discover our range of commercial coffee machines."
        ),
    }])
    from app.intelligence.facts import _market_line

    line = _market_line(facts[0])
    assert "£31.00" in line
    assert "BESTIR" not in line
    assert "304w" not in line


def test_a_call_to_action_drops_the_owner_channel_plan():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import repair_before_gate

    objective = "Prepare a UK business briefing for a coffee machine rental company."
    raw = (
        "Contact us today to start your coffee rental journey. "
        "Reach out to potential customers through direct outreach, online platforms, and partnerships."
    )
    repaired = repair_before_gate("call to action", raw, [], objective, "")
    assert "online platforms" not in repaired.lower()
    assert "Contact us today" in repaired
    assert section_rejection("call to action", repaired, "", objective) == ""


def test_your_doorstep_is_not_the_business_name():
    from app.intelligence.deliverable import borrowed_service_name

    blob = "https://example.co.uk/\nProfessional Care at Your Doorstep\nA home visit."
    assert borrowed_service_name("Book a visit at your doorstep.", "A home visit service.", blob) == ""


def test_a_rejected_rent_headline_is_a_customer_phrase():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import repair_before_gate

    objective = "Prepare a UK business briefing for a coffee machine rental company."
    advert = (
        "Headline: Reliable Coffee, No Upfront Cost\n"
        "Body: Rent a commercial coffee machine without buying one.\n"
        "Call to action: Contact us and ask about a visit."
    )
    fixed = repair_before_gate("advert", advert, [], objective, "")
    assert "Measure" not in fixed
    assert "Upfront" not in fixed
    assert "Rent a coffee machine instead of buying" not in fixed
    assert "without buying" in fixed
    assert section_rejection("advert", fixed, "", objective)


def test_a_later_page_price_is_not_crowded_out_by_titles(monkeypatch):
    from app.intelligence.deliverable import evidence_blob, section_rejection
    from app.intelligence.facts import extract_facts, label_relevance, repair_before_gate

    pages = [
        {
            "source_url": f"https://example.com/p{i}",
            "source_title": f"Kitchen page {i} title",
            "extracted_content": "No price is written on this page.",
        }
        for i in range(12)
    ]
    pages.append({
        "source_url": "https://refreshkitchen.co.uk/",
        "source_title": "Refresh Kitchen",
        "extracted_content": "PAY ON COMPLETION Refresh Your Kitchen can transform your kitchen in one day for only £950.",
    })
    facts = extract_facts(pages)
    assert facts[0]["quantity"] == "£950"
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")

    def complete(*_args, **_kwargs):
        return "\n".join(f"F{i} not relevant" for i in range(1, 13)), 1, {}

    labeled = label_relevance(facts, "The service replaces kitchen doors.", complete)
    assert any("950" in (fact.get("quantity") or "") for fact in labeled)
    objective = (
        "The service replaces kitchen doors, worktops and handles, and it avoids a full kitchen replacement."
    )
    blob = evidence_blob(pages)
    pricing = (
        "The price is set after measuring.\n"
        "Assumption: What still has to be measured: kitchen size, door count, door size, worktop length, worktop material, handle count."
    )
    repaired = repair_before_gate("pricing", pricing, labeled, objective, blob)
    assert "price each door" not in repaired
    assert section_rejection("pricing", repaired, blob, objective)


def test_an_empty_model_search_still_opens_the_service_query(monkeypatch):
    import app.intelligence.research as research_mod

    calls = []

    def search(query, limit=8):
        calls.append(query)
        if "coffee" in query and "rental" in query:
            return [{"title": "Coffee machine rental", "url": "https://brew.example/rental", "snippet": "coffee machine rental prices", "rerank_score": 0.9}]
        return [{"title": "no_results", "url": "", "snippet": query}]

    def fetch(url):
        return {"url": url, "title": "Coffee machine rental", "text": "Coffee machine rental from £19 per week.", "error": ""}

    monkeypatch.setattr(research_mod, "plan_queries", lambda *_args, **_kwargs: (["zzzz unrelated"], "model"))
    result = research_mod.research(
        "business_research",
        "Prepare a UK business briefing for a coffee machine rental company. Cover pricing.",
        "pkg-fallback",
        "research-e3",
        max_rounds=1,
        search_fn=search,
        fetch_fn=fetch,
    )
    assert result["evidence"]
    assert any("coffee" in query and "rental" in query for query in calls)
    assert any("brew.example" in (item.get("source_url") or "") for item in result["evidence"])


def test_owner_review_rejects_a_thin_plan():
    """A raw title, a page dump, one unnamed offer, a generic step, and a cited Assumption do not pass."""
    from app.intelligence.deliverable import evidence_blob, market_report_page, section_rejection
    from app.intelligence.facts import extract_facts, repair_before_gate

    title = "Replacement Kitchen Doors UK | Made to Measure | From £11.80"
    evidence = [{
        "source_url": "https://kitchenwarehouseltd.com/replacement-kitchen-doors",
        "source_title": title,
        "extracted_content": "Replacement kitchens made EASY!",
    }, {
        "source_url": "https://refreshkitchen.co.uk/",
        "source_title": "Refresh Kitchen",
        "extracted_content": "PAY ON COMPLETION Refresh Your Kitchen can transform your kitchen in one day for only £950.",
    }, {
        "source_url": "https://www.linkedin.com/pulse/coffee-cagr",
        "source_title": "Coffee Machine Market CAGR 4.2%",
        "extracted_content": "The market will grow at a CAGR of 4.2% to 6.8%.",
    }]
    facts = extract_facts(evidence)
    quotes = " ".join(fact.get("quote") or "" for fact in facts)
    assert "4.2" not in quotes
    assert market_report_page("https://www.linkedin.com/pulse/coffee-cagr", "Coffee Machine Market CAGR 4.2%")
    assert any("950" in (fact.get("quantity") or "") for fact in facts)
    assert all(" | " not in (fact.get("quote") or "") for fact in facts)
    blob = evidence_blob(evidence)
    objective = "The service replaces kitchen doors, worktops and handles, and it avoids a full kitchen replacement."
    raw_title = "Replacement Kitchen Doors UK | Made to Measure | From £11.80 (kitchenwarehouseltd.com) [F1]."
    assert "page title" in section_rejection("pricing", raw_title, blob, objective)
    dumped = "Competitors include Hale & Murray [F1].\nOpened sources\nURL: https://refreshkitchen.co.uk/\nPAY ON COMPLETION."
    assert "opened page text" in section_rejection("competitor and market research", dumped, blob, objective)
    listed = "Competitors include Hale & Murray [F1] and Kiss Kitchens [F2]."
    assert section_rejection("competitor and market research", listed, blob, objective)
    one_offer = "Refresh Kitchen states £950, and the page says pay on completion [F1]."
    assert "fewer than two" in section_rejection("competitor and market research", one_offer, blob, objective)
    generic_channels = "Online platforms, local directories, and word-of-mouth referrals."
    assert "timeframe" in section_rejection("channels", generic_channels, blob, objective)
    generic_steps = "Conduct market research and then develop the launch."
    assert "first week" in section_rejection("next steps", generic_steps, blob, objective)
    asked = (
        "Headline: Ask about coffee machine\n"
        "Body: The offer covers coffee machine rental and differs from buying outright.\n"
        "Call to action: Contact us and ask about a visit."
    )
    assert "complete phrase" in section_rejection(
        "advert", asked, blob, "The service is coffee machine rental for offices.",
    )
    cited = "Assumption: prices range from £1,000 to £12,000 [F1]."
    assert "cited fact" in section_rejection("pricing", cited, blob, objective)
    priced = repair_before_gate("pricing", "The price is set after measuring.", facts, objective, blob)
    assert priced == "The price is set after measuring."
    assert "price each door" not in priced
    assert section_rejection("pricing", priced, blob, objective)


def test_one_business_under_two_labels_is_not_two_competitors():
    """Refresh Kitchen and Refresh Your Kitchen are one business. A cost guide is not the second."""
    from app.intelligence.deliverable import section_rejection

    body = (
        'Refresh Kitchen states "Refresh Your Kitchen can transform a kitchen for £950." (refreshkitchen.co.uk).\n'
        'bestbuilders states "door replacement costs £10 to £40 per standard door." (www.bestbuilders.co.uk).'
    )
    blob = (
        "https://refreshkitchen.co.uk/ £950\n"
        "https://www.bestbuilders.co.uk/guides/costs/kitchen-door-replacement-cost £10 £40 per standard door"
    )
    objective = "The service replaces kitchen doors, and it avoids a full kitchen replacement."
    assert "fewer than two" in section_rejection("competitor and market research", body, blob, objective)


def test_a_breed_price_list_keeps_one_offer():
    """A page of breed prices is one offer, and the competitor section still cites that £."""
    from app.intelligence.deliverable import evidence_blob, section_rejection
    from app.intelligence.facts import extract_facts, repair_before_gate

    evidence = [{
        "source_url": "https://www.bluedogmobile.co.uk/prices",
        "source_title": "Blue Dog Mobile Grooming",
        "extracted_content": (
            "Prices by Dog Breed A Afghan Hound £95+ Airedale Terrier £73 Beagle £48 "
            "Border Collie £63 Bichon Frise £58"
        ),
    }, {
        "source_url": "https://www.themobilegroomroom.co.uk/",
        "source_title": "The Mobile Groom Room",
        "extracted_content": "The Mobile Groom Room offers a convenient dog grooming service at your door.",
    }]
    facts = extract_facts(evidence)
    quote = facts[0]["quote"]
    assert quote.count("£") == 1
    assert "95" in quote
    assert "48" not in quote
    objective = (
        "The service grooms dogs at the customer's home or in a van, and it is not a kennel."
    )
    blob = evidence_blob(evidence)
    repaired = repair_before_gate(
        "competitor and market research",
        "Groom & Zoom offers a door-to-door service.",
        facts,
        objective,
        blob,
    )
    assert "£95" not in repaired
    assert "£48" not in repaired and "£73" not in repaired
    assert section_rejection("competitor and market research", repaired, blob, objective)
    priced = repair_before_gate("pricing", "£48 to £95 for different breeds.", facts, objective, blob)
    assert "price is set after seeing the animal" not in priced.lower()
    assert section_rejection("pricing", priced, blob, objective)


def test_a_title_that_states_a_unit_keeps_that_unit():
    """From £2.70 Per Day is per day. A title with no unit still says the unit is missing."""
    from app.intelligence.facts import extract_facts

    stated = extract_facts([{
        "source_url": "https://www.nationwidecoffee.co.uk/",
        "source_title": "UK's #1 Commercial Coffee Machines Supplier | From £2.70 Per Day | Nationwide Coffee",
        "extracted_content": "Commercial coffee machines are available for rent or purchase.",
    }])
    assert stated[0]["quote"].lower().startswith("£2.70 per day")
    assert "does not give the size" not in stated[0]["quote"]
    missing = extract_facts([{
        "source_url": "https://kitchenwarehouseltd.com/replacement-kitchen-doors",
        "source_title": "Replacement Kitchen Doors UK | Made to Measure | From £11.80",
        "extracted_content": "Replacement kitchens made EASY!",
    }])
    assert "11.80" in missing[0]["quote"]
    assert "does not give the size" in missing[0]["quote"]
    assert "per " not in missing[0]["quote"].lower()


def test_channels_and_next_steps_are_not_filled_from_a_shared_sentence():
    """The same channel sentence for every trade is a template. The model writes it."""
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.execution import _section_jobs
    from app.intelligence.facts import _channel_sentence, _repair_next_steps, repair_before_gate

    assert _channel_sentence(DOOR) == ""
    assert _repair_next_steps(DOOR) == ""
    jobs = {job[0]: job for job in _section_jobs(DOOR)}
    assert jobs["channels"][3] == ""
    assert jobs["next steps"][3] == ""
    assert jobs["offer and positioning"][3] == ""
    assert jobs["pricing"][3] == ""
    prompt = " ".join(job[2] + job[3] for job in jobs.values()).lower()
    assert "posts on the website in the first week and phones" not in prompt
    assert "checkatrade" not in prompt
    shared_steps = (
        "In the first week the owner phones two suppliers and writes down the published price for the doors. "
        "In the first month the owner visits one kitchen and measures the door count."
    )
    assert "shared form" in section_rejection("next steps", shared_steps, "", DOOR)
    specific_steps = (
        "In the first week the owner photographs one worn kitchen and writes down the door count. "
        "In the first month the owner visits that kitchen and measures the worktop length."
    )
    assert section_rejection("next steps", specific_steps, "", DOOR) == ""
    filled = repair_before_gate("channels", shared_steps, [], DOOR, "")
    assert "posts on the website in the first week and phones" not in filled


def test_a_package_offer_names_what_the_visit_measures():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import repair_before_gate

    generic = "The offer is a package covering doors, worktops, handles, and it differs from a full replacement."
    assert "generic" in section_rejection("offer and positioning", generic, "", DOOR)
    fixed = repair_before_gate("offer and positioning", generic, [], DOOR, "")
    assert "The package replaces" not in fixed
    assert "The visit measures" not in fixed
    assert section_rejection("offer and positioning", fixed, "", DOOR)
    written = (
        "The package replaces the doors, the worktops, and the handles. "
        "The visit measures the kitchen size, the door count, the door size, the worktop length, "
        "the worktop material, and the handle count. It differs from a full replacement."
    )
    assert section_rejection("offer and positioning", written, "", DOOR) == ""
    short = "The offer is a package covering a coffee machine, and it differs from buying outright."
    coffee = "The service is coffee machine rental. Buying outright is the alternative."
    assert section_rejection("offer and positioning", short, "", coffee) == ""


def test_a_shouted_heading_is_paraphrased_with_the_citation():
    from app.intelligence.deliverable import evidence_blob, section_rejection
    from app.intelligence.facts import _market_line, extract_facts, repair_before_gate

    shouted = {
        "id": "F2",
        "entity": "Refresh Your Kitchen",
        "source": "https://refreshkitchen.co.uk/",
        "quote": (
            "PAY ON COMPLETION Refresh Your Kitchen are a Scottish based company "
            "who can transform your kitchen in one day for only £950."
        ),
        "quantity": "£950",
    }
    line = _market_line(shouted)
    assert not line.startswith("PAY")
    assert "£950" in line
    assert "[F2]" in line
    assert "the page says pay on completion" in line.lower()
    assert "can transform your kitchen" in line.lower()
    guide = {
        "id": "F1",
        "entity": "",
        "source": "https://www.bestbuilders.co.uk/guides/costs/kitchen-door-replacement-cost",
        "quote": "In 2026, kitchen door replacement in the UK costs £10-£40 per standard door, excluding handles.",
        "quantity": "£10-£40",
        "unit": "per standard door",
    }
    guide_line = _market_line(guide)
    assert "In 2026" in guide_line
    assert "per standard door" in guide_line
    assert "the page says" not in guide_line.lower()
    glued = {
        "id": "F3",
        "entity": "Premier Kitchen Makeovers",
        "source": "https://premierkitchenmakeovers.co.uk/",
        "quote": (
            "Upgrade Your Kitchen Easily with Stylish Replacement Doors and Worktops "
            "Transform your kitchen with replacement doors and worktops."
        ),
        "quantity": "",
    }
    premier = _market_line(glued)
    assert not premier.lower().startswith("upgrade your kitchen easily")
    assert "Transform your kitchen" in premier
    evidence = [{
        "source_url": "https://refreshkitchen.co.uk/",
        "source_title": "Refresh Kitchen",
        "extracted_content": shouted["quote"],
    }, {
        "source_url": "https://joesdoors.example/replace",
        "source_title": "Joe's Doors",
        "extracted_content": "We replace your existing doors and worktops, with no need for a full refit.",
    }]
    facts = extract_facts(evidence)
    pasted = (
        "PAY ON COMPLETION Refresh Your Kitchen are a Scottish based company who can transform "
        "your kitchen in one day for only £950.\n"
        "Joe's Doors replace existing doors and worktops."
    )
    blob = evidence_blob(evidence)
    assert "page heading" in section_rejection("competitor and market research", pasted, blob, DOOR)
    rebuilt = repair_before_gate("competitor and market research", pasted, facts, DOOR, blob)
    assert "PAY ON COMPLETION" not in rebuilt
    assert "This service differs by" not in rebuilt
    assert "Joe's Doors" in rebuilt
    assert section_rejection("competitor and market research", rebuilt, blob, DOOR)
    pasted_title = (
        "Joe's Doors (joesdoors.co.uk) Affordable Kitchen Makeovers A New Kitchen Look "
        "in as little as One Day Get the kitchen of your dreams [F8]."
    )
    title_blob = (
        "https://www.joesdoors.co.uk/\n"
        "Affordable Kitchen Makeovers A New Kitchen Look in as little as One Day Get the kitchen of your dreams"
    )
    assert "pastes the page" in section_rejection("competitor and market research", pasted_title, title_blob, DOOR)


def test_pricing_names_a_method_and_uses_a_unit_only_when_the_page_states_it():
    from app.intelligence.deliverable import evidence_blob, section_rejection
    from app.intelligence.facts import extract_facts, repair_before_gate

    evidence = [{
        "source_url": "https://www.bestbuilders.co.uk/guides/costs/kitchen-door-replacement-cost",
        "source_title": "Kitchen Door Replacement Cost",
        "extracted_content": "In 2026, kitchen door replacement in the UK costs £10-£40 per standard door, excluding handles.",
    }, {
        "source_url": "https://refreshkitchen.co.uk/",
        "source_title": "Refresh Kitchen",
        "extracted_content": "PAY ON COMPLETION Refresh Your Kitchen can transform your kitchen in one day for only £950.",
    }]
    facts = extract_facts(evidence)
    blob = evidence_blob(evidence)
    priced = repair_before_gate("pricing", "The price is set after measuring.", facts, DOOR, blob)
    assert "price each door" not in priced
    assert "Measure the kitchen size" not in priced
    assert section_rejection("pricing", priced, blob, DOOR)
    written = (
        "Best Builders (bestbuilders.co.uk) kitchen door replacement costs £10-£40 per standard door, excluding handles [F1].\n"
        "Assumption: price each door from the opened per door figures, price the worktop from the measured length and material, and count the handles. "
        "Measure the kitchen size, the door count, the door size, the worktop length, the worktop material, and the handle count before a price is written."
    )
    assert "per metre" not in written.lower()
    assert section_rejection("pricing", written, blob, DOOR) == ""
    bare = (
        "https://www.bestbuilders.co.uk/guides/costs/kitchen-door-replacement-cost states £10-£40 per standard door, excluding handles [F1].\n"
        "Assumption: doors, worktops, handles — what still has to be measured: kitchen size, door count, door size, "
        "worktop length, worktop material, and handle count."
    )
    assert "turn the measurements" in section_rejection("pricing", bare, blob, DOOR)


def test_a_postcode_is_not_an_opened_pound_figure():
    """M25 is a place. It is not the £25 on an opened page."""
    from app.intelligence.deliverable import _pricing_omits_page_figure, section_rejection

    body = (
        "Assumption: The weekly rate is based on a minimum rental period of one week.\n"
        "Assumption: The service includes delivery and collection within the M25 area at an additional cost."
    )
    blob = "https://coffeenerds.co.uk/pages/cost\nA managed contract is between £25 and £250 a week."
    omitted = _pricing_omits_page_figure(body, blob)
    assert "25" in omitted
    assert "does not use it" in section_rejection("pricing", body, blob, "A coffee machine rental company.")


def test_the_last_word_of_a_business_name_is_not_a_second_competitor():
    from app.intelligence.deliverable import _competitor_names, section_rejection

    body = (
        "Full House Coffee provides commercial machines starting at £31 a week [F2] (fullhousecoffee.com). "
        "Their service includes maintenance, differing from a short hire."
    )
    assert _competitor_names(body, "") == ["full house coffee"]
    blob = "https://www.fullhousecoffee.com/\nStarting from £31 a week."
    assert "fewer than two" in section_rejection(
        "competitor and market research", body, blob, "A coffee machine rental company.",
    )
    camel = "BestBuilders provides kitchen door replacements at £10 per standard door [F2]."
    assert "bestbuilders" in _competitor_names(camel, "")


def test_homeowners_across_is_not_a_business_name():
    from app.intelligence.deliverable import borrowed_service_name

    objective = "The service replaces kitchen doors, worktops and handles."
    blob = "Trusted by 30,000+ Homeowners Across the UK Replacement Kitchen Doors"
    assert borrowed_service_name(
        "30,000 homeowners across the UK replace doors in the first month.",
        objective,
        blob,
    ) == ""
    assert "Joe's Doors" in borrowed_service_name(
        "At Joe's Doors the owner uses a showroom.",
        objective,
        "At Joe's Doors we replace doors and worktops.",
    )


def test_a_curly_apostrophe_still_matches_the_cited_business():
    from app.intelligence.facts import enforce_citations

    facts = [{
        "id": "F5",
        "entity": "Tom's Coffee",
        "quote": "1 week (minimum) £495 plus vat",
        "source": "https://tomscoffee.co.uk/short-term-coffee-machine-hire/",
    }]
    text = "Tom\u2019s Coffee charges £495 for 1 week (minimum) plus vat [F5]."
    kept = enforce_citations(text, facts, "A coffee machine rental company.")
    assert "495" in kept
    assert "Tom" in kept


def test_a_tariff_quote_keeps_plus_vat():
    from app.intelligence.facts import _first_listed_price

    quote = "1 week (minimum) £495 plus vat 1 month £995 plus vat Delivery £300 plus vat"
    kept = _first_listed_price(quote)
    assert "495" in kept
    assert "plus vat" in kept.lower()
    assert "995" not in kept


def test_a_borrowed_name_is_deleted_and_the_model_sentence_stays():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import repair_before_gate

    objective = "The service replaces kitchen doors, worktops and handles."
    blob = "https://www.joesdoors.co.uk/\nAt Joe's Doors we replace doors and worktops.\n"
    text = "At Joe's Doors, the owner uses a local showroom to show replacement doors in the first month."
    repaired = repair_before_gate("channels", text, [], objective, blob)
    assert "Joe" not in repaired
    assert "showroom" in repaired
    assert "first month" in repaired
    assert section_rejection("channels", repaired, blob, objective) == ""
    still = "At Joe's Doors, the owner replaces doors."
    rejected = repair_before_gate("channels", still, [], objective, blob)
    assert "Joe's Doors" in rejected
    assert section_rejection("channels", rejected, blob, objective)


def test_registering_the_service_is_an_owner_action():
    from app.intelligence.deliverable import section_rejection

    objective = "The service is a mobile visit. Do not invent a service area."
    register = "Register the service on pettrader.co.uk and local community boards."
    reason = section_rejection("next steps", register, "", objective)
    assert "repeats the service" not in reason
    assert "first week" in reason
    scheduled = (
        "In the first week, the owner will schedule a trial session at a local park. "
        "In the first month, the owner will gather feedback from the customers."
    )
    assert section_rejection("next steps", scheduled, "", objective) == ""


def test_each_price_names_its_own_page():
    from app.intelligence.deliverable import claim_mismatch
    from app.intelligence.facts import name_the_page

    blob = (
        "https://cocosdoggrooming.co.uk/prices/\n"
        "Puppy grooms are £40.\n"
        "https://www.bluedogmobile.co.uk/prices\n"
        "Afghan Hound £95+\n"
    )
    mixed = (
        "Coco's Mobile Dog Grooming offers puppy grooms for £40 [F1], "
        "while Blue Dog Mobile Grooming charges £95 for an Afghan Hound [F2]."
    )
    reason = claim_mismatch(mixed, blob)
    assert "40" in reason
    assert "which opened page" in reason
    assert "95" not in reason or "which opened page" in reason
    one_block = "Guide prices are £40, £65 and £90 with no page named for each figure."
    many = (
        "https://cocosdoggrooming.co.uk/prices/\nPuppy grooms are £40.\n"
        "https://pettrader.co.uk/prices\nMobile prices are £40 to £90.\n"
        "https://www.groomeroo.co.uk/guides\nA full groom is £45 to £65.\n"
    )
    assert "one source" in claim_mismatch(one_block, many)
    facts = [
        {"id": "F1", "source": "https://refreshkitchen.co.uk/", "quote": "£950"},
        {"id": "F2", "source": "https://www.kitchenfaces.co.uk/pricing", "quote": "£1,300"},
    ]
    tagged = name_the_page(
        "Refresh Kitchen offers the work for £950 [F1], while Kitchen Faces charges £1,300 [F2].",
        facts,
    )
    assert "[F1] (refreshkitchen.co.uk)" in tagged
    assert "[F2] (kitchenfaces.co.uk)" in tagged
    assert tagged.rstrip(".").endswith("refreshkitchen.co.uk)") is False


def test_a_prepositional_headline_is_a_fragment():
    from app.intelligence.deliverable import plan_sections_filled, section_rejection, sections_for_gate
    from scripts.model_eval.score import score_plan

    body = "The service replaces kitchen doors, worktops and handles and differs from painting."
    cta = "Contact us today and ask for a visit."
    fragment = f"Headline: Without a Full Refit\nBody: {body}\nCall to action: {cta}"
    assert "complete phrase" in section_rejection("advert", fragment, "", DOOR)
    whole = f"Headline: New doors without a full refit\nBody: {body}\nCall to action: {cta}"
    assert section_rejection("advert", whole, "", DOOR) == ""
    plan = f"Advert\nHeadline: New doors without a full refit\nBody: {body}\n\nCall to action\n{cta}\n"
    joined = sections_for_gate(plan)["advert"]
    assert "Call to action:" in joined
    scored = score_plan(plan, DOOR, "", [])
    assert scored["section_reasons"].get("advert", "") == section_rejection("advert", joined, "", DOOR)
    assert plan_sections_filled(plan, "", DOOR) is False or "advert" not in scored["section_reasons"]


def test_a_channel_without_a_timeframe_names_that_gap():
    from app.intelligence.deliverable import section_rejection

    reason = section_rejection(
        "channels",
        "The owner promotes the service through the website by offering a rental option.",
        "",
        "The service is coffee machine rental.",
    )
    assert reason == "The channel section does not name a timeframe."


def test_a_cut_draft_keeps_the_continuation():
    from app.intelligence.execution import _draft_was_cut, _join_continuation

    cut = "Assumptions\nThe price follows the measurements. The"
    assert _draft_was_cut(cut, {"finish_reason": "length"})
    assert _draft_was_cut("Assumptions\nThe price follows the measurements.", {"finish_reason": "stop"}) is False
    joined = _join_continuation(
        cut,
        "The measured inputs set the price.\n\nUnresolved\nThe service area is still open.\n\nNothing was sent.",
    )
    assert " The\n" not in joined
    assert not joined.rstrip().endswith(" The")
    assert "Unresolved" in joined
    assert "The measured inputs set the price." in joined


def _door_sections(**overrides):
    from app.intelligence.deliverable import _REQUIRED

    sections = {key: "" for key in _REQUIRED}
    sections.update({
        "service": "The service replaces worn kitchen doors, worktops and handles for households that want to avoid a full kitchen replacement.",
        "target customer": "The customer is a household that wants worn kitchen doors, worktops and handles replaced.",
        "problem": "A full kitchen replacement costs more and takes longer when the doors, worktops and handles are the worn parts.",
        "offer and positioning": "The package replaces the kitchen doors, the worktops, and the handles. The visit measures the door count and the worktop length. It differs from a full kitchen replacement.",
        "competitor and market research": "Kitchen Door Co (https://doors.example/replace) states a standard door is £10 to £40 [F1].\nRefresh Kit (refreshkit.co.uk) charges £950, and the page says pay on completion [F2].\nThis service differs by replacing kitchen doors, worktops and handles rather than a full replacement.",
        "pricing": "https://doors.example/replace states kitchen doors are £10 to £40 [F1].\nhttps://refreshkit.co.uk/offer can transform your kitchen in one day for only £950, and the page states pay on completion [F2].\nAssumption: price each door from the opened per door figures, price the worktop from the measured length and material, and count the handles. Measure the kitchen size, the door count, the door size, the worktop length, the worktop material, and the handle count before a price is written.",
        "channels": "Households are reached through local search in the first week, where the owner posts one finished kitchen and asks for a reply.",
        "advert": "Headline: Fresh kitchen doors\nBody: The service replaces kitchen doors, worktops and handles and differs from a full replacement.\nCall to action: Ask for a visit.",
        "call to action": "Ask for a visit and a written scope before any booking is made.",
        "next steps": "In the first week the owner opens two supplier pages and writes down the prices they publish for doors, worktops and handles. In the first month the owner visits one kitchen and sets the price after measuring.",
        "assumptions": "The plan assumes a visit still has to see the doors, worktops and handles before any figure is used.",
        "unresolved": "Still open: the towns to visit for kitchen door replacement are not settled.",
    })
    sections.update(overrides)
    return sections


def _door_programme():
    from types import SimpleNamespace

    return SimpleNamespace(objective=DOOR, parent_id="", research={"evidence": [{
        "source_url": "https://doors.example/replace",
        "source_title": "Kitchen Door Co",
        "extracted_content": "Kitchen Door Co states kitchen doors are £10 to £40.",
    }, {
        "source_url": "https://refreshkit.co.uk/offer",
        "source_title": "Refresh Kit",
        "extracted_content": "Refresh Kit can transform your kitchen in one day for only £950, pay on completion.",
    }]}), (
        "https://doors.example/replace\nKitchen Door Co states kitchen doors are £10 to £40.\n"
        "https://refreshkit.co.uk/offer\nRefresh Kit can transform your kitchen in one day for only £950, pay on completion."
    )


def test_an_uncited_channel_price_is_deleted_and_the_sentence_stays(monkeypatch):
    from app.intelligence.execution import _fill_plan_sections

    reply = "The owner lists the service on the website, offering a first week rental at £495 plus VAT."
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        seen.append(user)
        return reply, 12, {"backend": "test", "finish_reason": "stop", "raw_response": reply}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme, blob = _door_programme()
    sections = _door_sections(channels="")
    rejected = _fill_plan_sections(programme, sections, blob)
    stored = sections["channels"]
    assert rejected == {}
    assert "£" not in stored
    assert "495" not in stored
    assert "website" in stored
    assert "lists" in stored
    assert "first week" in stored
    assert "had no [F#]" not in " ".join(seen)
    model_words = set(re.findall(r"[a-z0-9']+", reply.lower()))
    for word in re.findall(r"[a-z0-9']+", stored.lower()):
        assert word in model_words


def test_an_uncited_offer_price_retries_the_package_check(monkeypatch):
    from app.intelligence.execution import _fill_plan_sections

    missing = (
        "The package replaces the kitchen doors and the worktops for £950. "
        "The visit measures the door count and the worktop length. "
        "It differs from a full kitchen replacement."
    )
    passing = (
        "The package replaces the kitchen doors, the worktops, and the handles. "
        "The visit measures the door count and the worktop length. "
        "It differs from a full kitchen replacement."
    )
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        seen.append(user)
        text = missing if len(seen) == 1 else passing
        return text, 12, {"backend": "test", "finish_reason": "stop", "raw_response": text}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme, blob = _door_programme()
    sections = _door_sections(**{"offer and positioning": ""})
    rejected = _fill_plan_sections(programme, sections, blob)
    assert rejected == {}
    assert "handles" in sections["offer and positioning"]
    assert "£" not in sections["offer and positioning"]
    assert len(seen) == 2
    assert "handles" in seen[1].lower()
    assert "had no [F#]" not in seen[1]
    assert "Cite that fact as [F#]" not in seen[1]


def test_a_mixed_competitor_price_keeps_the_one_source_reason(monkeypatch):
    from app.intelligence.execution import _fill_plan_sections

    mixed = "The opened pages list replacement prices of £10 and £950 for this kitchen work."
    passing = (
        "Kitchen Door Co (https://doors.example/replace) states a standard door is £10 to £40 [F1].\n"
        "Refresh Kit (refreshkit.co.uk) charges £950, and the page says pay on completion [F2].\n"
        "This service differs by replacing kitchen doors, worktops and handles rather than a full replacement."
    )
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        seen.append(user)
        text = mixed if len(seen) == 1 else passing
        return text, 12, {"backend": "test", "finish_reason": "stop", "raw_response": text}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme, blob = _door_programme()
    sections = _door_sections(**{"competitor and market research": ""})
    rejected = _fill_plan_sections(programme, sections, blob)
    assert rejected == {}
    assert len(seen) == 2
    assert "one claim, one source" in seen[1].lower()
    assert "had no [F#]" not in seen[1]


def test_a_price_note_that_the_page_is_silent_keeps_the_price():
    from app.intelligence.deliverable import section_rejection

    blob = (
        "https://refreshkitchen.co.uk/\n"
        "Refresh Your Kitchen can transform your kitchen in one day for only £950. PAY ON COMPLETION\n"
        "https://www.kitchenfaces.co.uk/pricing\n"
        "Kitchen Door Replacement Pricing | From £1,300 | Kitchen Faces\n"
    )
    body = (
        "Refresh Kitchen offers a full kitchen transformation for £950 PAY ON COMPLETION [F1] (refreshkitchen.co.uk). "
        "Kitchen Faces charges £1,300 for replacement doors and worktops, though the page does not specify the size or materials included [F2] (kitchenfaces.co.uk). "
        "This service differs by replacing doors, worktops and handles rather than a full kitchen replacement."
    )
    reason = section_rejection("competitor and market research", body, blob, DOOR)
    assert "fewer than two" not in reason
    assert "worktop" in reason


def test_a_week_price_is_not_a_day_because_the_page_says_cups_per_day():
    from app.intelligence.deliverable import claim_mismatch

    blob = (
        "https://www.fullhousecoffee.com/\n"
        "Starting From £31.00/week BESTIR BM50 - 100 Cups Per Day\n"
    )
    wrong = "Full House Coffee provides machines starting from £31.00 per day [F2] (fullhousecoffee.com)."
    reason = claim_mismatch(wrong, blob)
    assert "per day" in reason
    assert "per week" in reason
    right = "Full House Coffee provides machines starting from £31.00 a week [F2] (fullhousecoffee.com)."
    assert claim_mismatch(right, blob) == ""


def test_a_ten_word_headline_is_retried_as_a_headline(monkeypatch):
    from app.intelligence.execution import _fill_plan_sections

    long_headline = (
        "Headline: Replace worn kitchen doors worktops and handles this week now\n"
        "Body: The service replaces kitchen doors, worktops and handles and differs from a full replacement.\n"
        "Call to action: Ask for a visit and a written scope before any booking is made.\n"
    )
    short = "Fresh kitchen doors without a full refit"
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        seen.append(user)
        text = long_headline if len(seen) == 1 else short
        return text, 12, {"backend": "test", "finish_reason": "stop", "raw_response": text}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme, blob = _door_programme()
    sections = _door_sections(advert="")
    rejected = _fill_plan_sections(programme, sections, blob)
    assert rejected == {}
    assert sections["advert"].startswith("Headline: Fresh kitchen doors")
    assert len(seen) == 2
    assert "under 10 words" in seen[1]
    assert "needs a headline, one body" not in seen[1]


def test_a_second_business_is_not_part_of_the_first_price():
    from app.intelligence.deliverable import claim_mismatch, section_rejection
    from app.intelligence.facts import name_the_page, repair_before_gate

    blob = (
        "https://refreshkitchen.co.uk/\n"
        "Refresh Your Kitchen can transform your kitchen in one day for only £950. PAY ON COMPLETION\n"
        "https://www.joesdoors.co.uk/replacement-kitchen-doors/\n"
        "We replace your existing doors, drawer fronts, and worktops.\n"
        "https://www.kitchenfaces.co.uk/pricing\n"
        "Kitchen Door Replacement Pricing | From £1,300 | Kitchen Faces\n"
    )
    facts = [
        {"id": "F1", "source": "https://refreshkitchen.co.uk/", "quote": "£950 PAY ON COMPLETION"},
        {"id": "F4", "source": "https://www.joesdoors.co.uk/replacement-kitchen-doors/", "quote": "doors, drawer fronts, and worktops"},
        {"id": "F2", "source": "https://www.kitchenfaces.co.uk/pricing", "quote": "From £1,300"},
    ]
    paired = (
        "Refresh Kitchen offers a full kitchen transformation for £950 PAY ON COMPLETION [F1], "
        "while Joe's Doors provides replacement doors, drawer fronts, and worktops without the need for a full refit [F4]. "
        "Unlike these options, this service replaces doors, worktops and handles rather than a full kitchen replacement."
    )
    named = name_the_page(paired, facts)
    assert claim_mismatch(named, blob) == ""
    assert section_rejection("competitor and market research", named, blob, DOOR) == ""
    mixed = (
        "Refresh Kitchen offers a full kitchen transformation for £950 PAY ON COMPLETION [F1], "
        "while Kitchen Faces charges £1,300 for their kitchen door and worktop solutions [F2]. "
        "Unlike these options, replacing just doors and worktops with Joe's Doors provides a cost-effective way to refresh the kitchen without a full rebuild [F4]."
    )
    repaired = repair_before_gate("competitor and market research", mixed, facts, DOOR, blob)
    assert "worktop solutions" not in repaired.lower()
    assert "£1,300" in repaired
    assert "£950" in repaired
    assert section_rejection("competitor and market research", repaired, blob, DOOR) == ""
    advert = (
        "Headline: Transform Your Kitchen with Doors, Worktops, and Handles\n"
        "Body: Replace your kitchen without a full refit, saving time and cost.\n"
        "Call to action: Contact us today to get started on your kitchen upgrade."
    )
    kept = repair_before_gate("advert", advert, facts, DOOR, blob)
    assert "saving" not in kept.lower()
    assert "Headline:" in kept
    assert "Call to action:" in kept
    assert section_rejection("advert", kept, blob, DOOR) == ""


def test_a_lowercase_description_does_not_name_another_site():
    from app.intelligence.deliverable import _names_opened_page, claim_mismatch
    from app.intelligence.facts import enforce_citations, name_the_page

    blob = (
        "https://www.nationwidecoffee.co.uk/\n"
        "Nationwide Coffee £2.70 per day.\n"
        "https://www.coffeemachinerentals.co.uk/\n"
        "prices ranging from £1,000 to £12,000\n"
        "https://coffeenerds.co.uk/pages/cost\n"
        "Most UK businesses renting a commercial coffee machine pay between £19 and £96 per week.\n"
        "https://stafco.co.uk/\n"
        "StafCo Coffee Machine Rentals\n"
    )
    facts = [
        {"id": "F1", "entity": "Nationwide Coffee", "source": "https://www.nationwidecoffee.co.uk/", "quote": "£2.70 per day."},
        {"id": "F2", "entity": "coffeemachinerentals", "source": "https://www.coffeemachinerentals.co.uk/", "quote": "prices ranging from £1,000 to £12,000"},
        {"id": "F3", "entity": "coffeenerds", "source": "https://coffeenerds.co.uk/pages/cost", "quote": "Most UK businesses renting a commercial coffee machine pay between £19 and £96 per week."},
    ]
    sentence = (
        "Nationwide Coffee offers coffee machine rentals at £2.70 per day [F1], "
        "while Coffee Nerds provides weekly rental rates starting from £19 [F3]."
    )
    kept = enforce_citations(sentence, facts, "A coffee machine rental company.")
    assert "2.70" in kept
    assert "19" in kept
    assert "Coffee Nerds" in kept
    assert claim_mismatch(name_the_page(kept, facts), blob) == ""
    assert _names_opened_page(
        "offers coffee machine rentals at £2.70 per day",
        "https://www.coffeemachinerentals.co.uk/",
    ) is False
    assert _names_opened_page("Nationwide Coffee offers rentals", "https://www.nationwidecoffee.co.uk/") is True
    assert _names_opened_page(
        "coffeemachinerentals offers machines from £1,000",
        "https://www.coffeemachinerentals.co.uk/",
    ) is True
    blamed = "At StafCo, businesses can rent coffee machines for as little as £19 per week [F3] (coffeenerds.co.uk)."
    assert "which opened page" in claim_mismatch(blamed, blob)


def test_an_invented_offer_title_does_not_drop_the_package():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import enforce_citations

    objective = "The service replaces kitchen doors, worktops and handles, and it avoids a full kitchen replacement."
    text = (
        "The Kitchen Revival Offer includes doors, worktops, and handles, leaving out structural changes and major fittings. "
        "The visit measures the existing space to custom-fit the new elements, differing from a full kitchen renovation by focusing solely on surface updates."
    )
    kept = enforce_citations(text, [], objective)
    assert "Kitchen Revival" not in kept
    assert "doors" in kept and "worktops" in kept and "handles" in kept
    assert section_rejection("offer and positioning", kept, "", objective) == ""


def test_an_upfront_cost_clause_is_deleted_and_the_advert_stays():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import repair_before_gate

    advert = (
        "Headline: The service offers flexible coffee machine rental options.\n"
        "Body: The service ensures your business has access to premium equipment without the upfront cost.\n"
        "Call to action: Contact us today to find the perfect coffee solution for your needs."
    )
    kept = repair_before_gate("advert", advert, [], "A coffee machine rental company.", "")
    assert "upfront" not in kept.lower()
    assert "premium equipment" in kept
    assert "Headline:" in kept and "Body:" in kept and "Call to action:" in kept
    assert section_rejection("advert", kept, "", "A coffee machine rental company.") == ""


def test_a_free_quote_clause_is_deleted_and_the_ask_stays():
    from app.intelligence.deliverable import section_rejection
    from app.intelligence.facts import enforce_citations

    text = (
        "30,000+ homeowners how replacing your doors, worktops, and handles can transform your kitchen without the hassle of a full refit. "
        "Contact us today for a free quote."
    )
    kept = enforce_citations(text, [], DOOR)
    assert "free quote" not in kept.lower()
    assert "+" not in kept
    assert "30" not in kept
    assert "Contact us today" in kept
    assert "doors" in kept
    assert section_rejection("call to action", kept, "", DOOR) == ""
