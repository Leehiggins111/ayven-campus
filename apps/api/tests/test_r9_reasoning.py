"""R9: reasoning must not reach tool arguments or deliverable text."""

import json

from app.intelligence.boundary import reject_reasoning_query, separate_channels, validate_tool_call
from app.intelligence.think import strip_think, visible_message
from app.models import ollama_answer, ollama_generation


SECRET = "secret-plan-marker"


def test_unclosed_think_is_removed_from_every_public_path():
    raw = f"<think>{SECRET} and the rest of the plan"
    assert SECRET not in strip_think(raw)
    assert SECRET not in separate_channels(raw).executable
    assert reject_reasoning_query(raw) == "reasoning_marker"
    assert SECRET not in ollama_answer({"message": {"content": raw, "reasoning": SECRET}})
    gate = validate_tool_call({"name": "web_search", "arguments": json.dumps({"query": raw})})
    assert gate.ok is False
    assert SECRET not in json.dumps(gate.call)


def test_think_inside_a_json_string_is_not_a_tool_argument():
    raw = json.dumps({"query": f"scottish doors <think>{SECRET}</think>"})
    gate = validate_tool_call({"name": "web_search", "arguments": raw})
    assert gate.ok is False
    assert gate.error == "reasoning_marker"
    assert SECRET not in json.dumps(gate.call)
    assert SECRET not in strip_think(raw)


def test_harmony_analysis_channel_is_not_kept():
    raw = f"<|channel|>analysis<|message|>{SECRET}<|end|>The listing is a draft."
    cleaned = strip_think(raw)
    assert SECRET not in cleaned
    assert "The listing is a draft." in cleaned
    assert reject_reasoning_query(raw) == "reasoning_marker"
    channels = separate_channels(raw)
    assert SECRET not in channels.executable or reject_reasoning_query(channels.executable)


def test_planning_prose_and_think_mp3_are_rejected():
    prose = "Okay, let's tackle this query by searching for the licence first."
    assert reject_reasoning_query(prose) == "planning_prose"
    assert reject_reasoning_query("think.mp3") == "reasoning_artifact"
    gate = validate_tool_call({"name": "web_search", "arguments": json.dumps({"query": "think.mp3"})})
    assert gate.ok is False


def test_empty_content_does_not_adopt_the_reasoning_field():
    payload = {"choices": [{"message": {"content": "", "reasoning_content": SECRET}}]}
    assert ollama_answer(payload) == ""
    assert visible_message({"content": "", "reasoning": SECRET, "thinking": SECRET}) == ""
    assert SECRET not in ollama_answer({"message": {"content": None, "reasoning_content": SECRET}})


def test_deliverable_text_drops_the_same_markers():
    draft = f"Payhip draft.\n<think>{SECRET}</think>\nPrice £9.00.\n<|channel|>analysis<|message|>{SECRET}"
    public = strip_think(draft)
    assert SECRET not in public
    assert "Price £9.00." in public


def test_load_duration_is_recorded_on_the_model_call():
    timing = ollama_generation({"load_duration": 1_500_000_000, "eval_count": 4})
    assert timing["load_s"] == 1.5
