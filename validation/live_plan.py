"""Plan for the single-model live test. This module does not download or spend.

The primary server is llama.cpp llama-server with Qwen3-30B-A3B-Instruct-2507.
Load time is recorded only when a caller supplies the clock readings.
"""

from __future__ import annotations

from app.intelligence.gpu_lifecycle import (
    COMMUNITY_HARD_CAP_USD,
    MAX_WALL_S,
    SECURE_HARD_CAP_USD,
    delete_command,
    self_terminate_preamble,
)

PRIMARY_MODEL = "Qwen3-30B-A3B-Instruct-2507"
GGUF_REPO = "bartowski/Qwen_Qwen3-30B-A3B-Instruct-2507-GGUF"
GGUF_FILE = "Qwen_Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
FALLBACK_AWQ = "Sophia-AI/Qwen3-30B-A3B-Instruct-2507-AWQ-W4A16"


def llama_server_args(model_path: str) -> list[str]:
    """One resident model for every role. --reasoning off avoids the false thinking detector."""
    return [
        "llama-server",
        "-m",
        model_path,
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "-ngl",
        "99",
        "-c",
        "16384",
        "-fa",
        "on",
        "--jinja",
        "--reasoning",
        "off",
        "--parallel",
        "1",
    ]


def record_load(events: dict) -> dict:
    """Turn clock readings into seconds. Missing readings stay unmeasured."""

    def span(start: str, end: str):
        left = events.get(start)
        right = events.get(end)
        if left is None or right is None:
            return None
        return round(float(right) - float(left), 3)

    return {
        "model": PRIMARY_MODEL,
        "engine": "llama-server",
        "download_s": span("download_started", "download_finished"),
        "load_s": span("load_started", "ready"),
        "first_token_s": span("ready", "first_token"),
        "measured": events.get("ready") is not None,
    }


# DashScope BFCL scores used native function calling. They are not a measurement
# of Q4_K_M on llama.cpp. Treating them as one is an assumption.
BFCL_ASSUMPTION = (
    "BFCL figures came from DashScope's API with native function calling. "
    "They do not transfer directly to Q4_K_M on llama.cpp. This is an assumption. UNVERIFIED."
)

# Community prices noted 13 Oct 2026 at 11:30. Not a live quote from this run.
COMMUNITY_PRICES_USD = {"noted_at": "2026-10-13T11:30Z", "3090": 0.22, "4090": 0.34, "A6000": 0.33}

STEP_TWO_MODEL = "Qwen3-8B"
STEP_THREE_MODEL = "Qwen3-32B"


def llama_server_args_8b(model_path: str) -> list[str]:
    """Step 2 only. Thinking stays on, and R9 must still strip it before a tool sees it."""
    return [
        "llama-server",
        "-m",
        model_path,
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "-ngl",
        "99",
        "-c",
        "16384",
        "-fa",
        "on",
        "--jinja",
        "--reasoning",
        "on",
        "--parallel",
        "1",
    ]


def escalation_order() -> list[dict]:
    """Backup order. Nothing here is enabled. No paid external model is the default."""
    return [
        {
            "step": 1,
            "model": PRIMARY_MODEL,
            "weights": "Q4_K_M",
            "context": 16384,
            "reasoning": "off",
            "engine": "llama-server",
            "status": "UNVERIFIED",
            "enabled_by_default": False,
            "in_this_session": True,
        },
        {
            "step": 2,
            "model": STEP_TWO_MODEL,
            "weights": "BF16",
            "vram_gb": 16,
            "fits": "24 GB 4090",
            "community_usd_per_hour": 0.34,
            "reasoning": "on",
            "when": "The director plan test fails on step 1.",
            "requires": "R9 strip tests pass on the thinking output before any thinking text is trusted.",
            "status": "UNVERIFIED",
            "enabled_by_default": False,
            "in_this_session": True,
        },
        {
            "step": 3,
            "model": STEP_THREE_MODEL,
            "weights": "Q4_K_M",
            "size_gb": 19.8,
            "fits_24gb_context": 8192,
            "otherwise_gpu": "48 GB A6000",
            "community_usd_per_hour": 0.33,
            "when": "Only after steps 1 and 2. The live script does not start it.",
            "status": "UNVERIFIED",
            "enabled_by_default": False,
            "in_this_session": False,
        },
    ]


def steps_for_session(director_plan_passed: bool) -> list[int]:
    """Steps 1 and 2 share one cap and one pod. Step 3 stays off."""
    if director_plan_passed:
        return [1]
    return [1, 2]


def session_plan() -> dict:
    return {
        "status": "UNVERIFIED",
        "paid_external_by_default": False,
        "same_cap_usd": COMMUNITY_HARD_CAP_USD,
        "same_session_steps": [1, 2],
        "step_3_in_session": False,
        "bfcl_assumption": BFCL_ASSUMPTION,
        "community_prices_usd": COMMUNITY_PRICES_USD,
        "order": escalation_order(),
    }


# Written before any live run. These are the gates. They are not results.
ACCEPTANCE_CRITERIA = [
    "50 constrained tool calls per role: at least 98 percent valid the first time, 100 percent after one repair, and zero reasoning strings reaching a tool.",
    "20 irrelevance prompts: at most 2 tool calls.",
    "10 missing-tool prompts: at least 8 of 10 end in cannot or escalate.",
    "10 director plans.",
    "QA planted-defect set of 10: at least 8 caught.",
    "Record latency, tokens, and pounds for the run.",
]

MULTI_STEP_TASK = {
    "id": "weakness-multistep",
    "why": "A long chain of tool steps is where this model drops a constraint or repeats itself.",
    "steps": [
        "Search once for a public page about Scottish short-term let guest records.",
        "Fetch that one page.",
        "Calculate 9.00 minus 2.98. The calculator result is a check, not a price to publish.",
        "Write one sentence that uses only a sentence from the fetched page.",
        "Stop. Do not send or publish.",
    ],
    "tools": ["web_search", "fetch_page", "calculator"],
    "acceptance": "Each tool call is valid JSON. Code checks the calculator. The sentence adds no claim that was not on the page.",
}


def plan() -> dict:
    return {
        "mode": "plan",
        "spend": False,
        "model": PRIMARY_MODEL,
        "engine": "llama-server",
        "gguf": GGUF_FILE,
        "context": 16384,
        "reasoning": "off",
        "community_cap_usd": COMMUNITY_HARD_CAP_USD,
        "secure_cap_usd": SECURE_HARD_CAP_USD,
        "wall_s": int(MAX_WALL_S),
        "stop": "delete",
        "delete_command": delete_command(),
        "self_terminate": self_terminate_preamble(),
        "fallback_awq": FALLBACK_AWQ,
        "confirm_env": "AYVEN_CONFIRM_SPEND=YES",
        "load": record_load({}),
        "session": session_plan(),
        "acceptance_criteria": ACCEPTANCE_CRITERIA,
        "multi_step_task": MULTI_STEP_TASK,
        "paid_external_by_default": False,
    }
