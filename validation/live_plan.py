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
    }
