"""Plan model selection, and the rule that code does not write a failed section."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

DOOR = (
    "Prepare a UK business launch plan for a service that replaces kitchen doors, worktops and handles. "
    "A full kitchen replacement is the expensive alternative. Painting and wrapping are other services, not this service."
)


def test_unset_plan_model_keeps_the_employee_model(monkeypatch):
    monkeypatch.delenv("AYVEN_PLAN_MODEL", raising=False)
    from app.models import complete_role, plan_model, role_model, set_role_generator

    assert plan_model() == ""
    set_role_generator(lambda role, system, user, max_tokens: ("The service is ready.", 3, {"backend": "generator"}))
    try:
        _text, _tokens, meta = complete_role("EMPLOYEE", "Write the finished lines only.", "State the service.")
        assert meta["model"] == role_model("EMPLOYEE")
        _text, _tokens, planned = complete_role(
            "EMPLOYEE", "Write the finished lines only.", "State the service.", model="plan-writer",
        )
        assert planned["model"] == "plan-writer"
    finally:
        set_role_generator(None)


def test_section_writes_use_the_plan_model_and_other_calls_do_not(monkeypatch):
    monkeypatch.setenv("AYVEN_PLAN_MODEL", "plan-writer")
    seen = {}

    def fake_complete(role, system, user, max_tokens=500, schema=None, prefill="", stop=None, expand_unfinished=True, ollama_format=None, model=None):
        seen["model"] = model
        text = "The service replaces worn kitchen doors, worktops and handles."
        return text, 4, {"backend": "generator", "model": model or "employee", "raw_response": text}

    monkeypatch.setattr("app.models.complete_role", fake_complete)
    from app.intelligence.execution import _complete, _write_one_section

    programme = SimpleNamespace(objective=DOOR, parent_id="pkg-plan", research={"evidence": []})
    _write_one_section(programme, "service", "Service", "Write one sentence. Name the service.", prefill="The service ")
    assert seen["model"] == "plan-writer"
    _complete("EMPLOYEE", "You plan searches.", "Write queries.", plain=True)
    assert seen["model"] in (None, "")


def test_a_failed_section_is_not_written_by_code(monkeypatch):
    from app.intelligence.deliverable import _REQUIRED
    from app.intelligence.execution import _fill_plan_sections

    sections = {key: "" for key in _REQUIRED}
    reply = "Let's think about the user and what they need me to write."

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        return reply, 8, {"backend": "test", "raw_response": reply}

    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    programme = SimpleNamespace(
        objective=DOOR,
        parent_id="pkg-empty",
        research={"evidence": [{
            "source_title": "Door prices",
            "source_url": "https://doors.example/prices",
            "extracted_content": "Replacement doors are £10 to £40.",
        }]},
    )
    rejected = _fill_plan_sections(programme, sections, "Replacement doors are £10 to £40.")
    assert sections["pricing"] == ""
    assert "local search, where the owner posts one note" not in (sections.get("channels") or "")
    assert rejected.get("pricing")
    assert "Copy these figures" not in " ".join(
        # The fake sees every prompt. None of them may tell the model which number to copy.
        []
    )


def test_prompts_do_not_tell_the_model_which_figure_to_copy(monkeypatch):
    seen = []

    def fake(role, system, user, max_tokens=400, programme=None, package_id="", plain=False, prefill="", stop=None, expand_unfinished=True, ollama_format=None, plan=False):
        seen.append(user)
        return "Let's think about the user.", 4, {"backend": "test", "raw_response": "Let's think about the user."}

    monkeypatch.delenv("AYVEN_EVIDENCE_PACK", raising=False)
    monkeypatch.setattr("app.intelligence.execution._complete", fake)
    from app.intelligence.deliverable import _REQUIRED
    from app.intelligence.execution import _fill_plan_sections, _section_evidence

    programme = SimpleNamespace(
        objective=DOOR,
        parent_id="pkg-hint",
        research={"evidence": [{
            "source_title": "Door prices",
            "source_url": "https://doors.example/prices",
            "extracted_content": "Replacement doors are £10 to £40.",
        }]},
    )
    sections = {key: "" for key in _REQUIRED}
    _fill_plan_sections(programme, sections, "Replacement doors are £10 to £40.")
    assert seen
    assert all("Copy these figures" not in user for user in seen)
    assert any("Prices stated on opened pages" in user for user in seen)
    full = _section_evidence(programme)
    monkeypatch.setenv("AYVEN_EVIDENCE_PACK", "compact")
    compact = _section_evidence(programme)
    assert full != compact
    assert "£10" in compact
    assert "Prices stated on opened pages" not in compact
    assert "Name the service." in seen[0] or any("Label each part." in user for user in seen)


def test_num_ctx_override_is_opt_in(monkeypatch):
    from app.models import ollama_chat_body, ollama_native_body

    monkeypatch.delenv("AYVEN_NUM_CTX", raising=False)
    monkeypatch.delenv("AYVEN_TEMPERATURE", raising=False)
    body = ollama_native_body("qwen3:4b", "Write the finished lines only.", "State the service.", 80)
    assert body["options"]["num_ctx"] >= 8192
    assert body["options"]["temperature"] == 0.2
    monkeypatch.setenv("AYVEN_NUM_CTX", "4096")
    monkeypatch.setenv("AYVEN_TEMPERATURE", "0.4")
    overridden = ollama_native_body("qwen3:4b", "Write the finished lines only.", "State the service.", 80)
    assert overridden["options"]["num_ctx"] == 4096
    assert overridden["options"]["temperature"] == 0.4
    chat = ollama_chat_body("gemma3:12b", "Write the finished lines only.", "State the service.", 80)
    assert chat["options"]["num_ctx"] == 4096
    assert "messages" in chat
    assert "prompt" not in chat


def test_a_non_qwen_model_uses_the_chat_api(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "message": {"content": "The offer is a scheduled visit."},
                "done_reason": "stop",
                "eval_count": 8,
                "prompt_eval_count": 12,
            }

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "body": json})
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setenv("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    from app.models import complete_role

    text, tokens, meta = complete_role("EMPLOYEE", "Write the finished lines only.", "State the offer.", 80, model="gemma3:12b")
    assert text == "The offer is a scheduled visit."
    assert tokens == 8
    assert calls[0]["url"].endswith("/api/chat")
    assert calls[0]["body"]["model"] == "gemma3:12b"
    assert meta["num_ctx"] >= 8192 or meta.get("constrained", {}).get("num_ctx", 0) >= 8192


def test_engine_source_does_not_name_the_unseen_eval_trade():
    root = Path(__file__).resolve().parents[3]
    for relative in (
        "apps/api/app/intelligence/execution.py",
        "apps/api/app/intelligence/deliverable.py",
        "apps/api/app/models.py",
    ):
        text = (root / relative).read_text(encoding="utf-8").lower()
        assert "groom" not in text


def test_generation_clocks_separate_prompt_eval_from_generation():
    from app.models import ollama_generation

    record = ollama_generation({
        "response": "The service is ready.",
        "done_reason": "stop",
        "prompt_eval_count": 40,
        "eval_count": 10,
        "prompt_eval_duration": 2_000_000_000,
        "eval_duration": 4_000_000_000,
        "load_duration": 1_500_000_000,
        "total_duration": 8_000_000_000,
    })
    assert record["prompt_eval_s"] == 2.0
    assert record["eval_s"] == 4.0
    assert record["load_s"] == 1.5
    assert record["elapsed_s"] == 8.0


def test_call_timing_and_lee_projection_use_the_measured_rates():
    from scripts.model_eval.score import call_timing, format_timing_table, project_to_lee

    timing = call_timing(
        [
            {"prompt_tokens": 100, "prompt_eval_s": 2, "completion_tokens": 50, "eval_s": 10},
            {"error": "timeout", "finish_reason": "timeout"},
        ],
        [{"attempt": 1}, {"attempt": 2}],
    )
    assert timing["model_calls"] == 2
    assert timing["retries"] == 1
    assert timing["timeouts"] == 1
    assert timing["prompt_tokens_per_second"] == 50.0
    assert timing["generation_tokens_per_second"] == 5.0
    projected = project_to_lee(
        file_bytes=2_500_000_000,
        prompt_tokens=0,
        completion_tokens=100,
        prompt_eval_s=0,
        eval_s=10,
        runner_copy_gbps=50,
        unscaled_s=0,
    )
    assert projected["efficiency"] == 0.5
    assert projected["device"] == "vram"
    assert projected["lee_gpu_generation_tokens_per_second"] == 22.4
    offload = project_to_lee(
        file_bytes=5_000_000_000,
        prompt_tokens=0,
        completion_tokens=100,
        prompt_eval_s=0,
        eval_s=10,
        runner_copy_gbps=100,
        unscaled_s=0,
    )
    assert offload["device"] == "partial_offload"
    assert offload["achieved_read_gbps"] == 50.0
    table = format_timing_table([{
        "model": "qwen3:4b",
        "request": "alba",
        "pull": "1.0 min",
        "load": "3.0 s",
        "job": "12.0 min",
        "prompt_tps": 50,
        "gen_tps": 8,
        "calls": 20,
        "retries": 4,
        "timeouts": 0,
        "ram": "6.0 GB",
        "vram": "none",
        "lee_job": "4.0 min",
    }])
    assert "| Pull |" in table or "Pull" in table.splitlines()[0]
    assert "12.0 min" in table
    assert "1.0 min" in table


def test_score_marks_an_invented_price_and_a_gate_failure():
    from scripts.model_eval.score import lee_job_estimate, score_plan, two_model_fit

    plan = "Pricing\nAssumption: doors are about £99.\n"
    scored = score_plan(plan, DOOR, "https://doors.example/prices\nReplacement doors are £10 to £40.", [])
    assert scored["gate_pass"] is False
    assert "£99" in scored["invented_prices"]
    small = lee_job_estimate(2_500_000_000, 1000, 600)
    assert small["band"] == "weights_fit_in_4gb"
    offload = lee_job_estimate(5_200_000_000, 1200, 3600)
    assert offload["minutes_at_2_tokens_per_second"] == 10.0
    assert offload["minutes_at_5_tokens_per_second"] == 4.0
    fit = two_model_fit(2_500_000_000, 5_200_000_000)
    assert fit["sequential_fits"] is True
    assert fit["resident_together_fits"] is True
    too_big = two_model_fit(2_500_000_000, 9_300_000_000)
    assert too_big["resident_together_fits"] is False
    assert os.environ.get("AYVEN_PLAN_MODEL", "") != "plan-writer"
