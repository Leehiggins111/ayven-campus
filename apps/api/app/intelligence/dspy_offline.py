"""Offline prompt optimisation. Production never rewrites its own prompts.

DSPy (GEPA, MIPROv2) can be used by a human on a held-out set. This module
only proposes a candidate version and marks it unapproved.
"""

from __future__ import annotations

import json
from pathlib import Path


def score_example(expected_labels: list[str], output: str) -> float:
    text = (output or "").lower()
    if not expected_labels:
        return 0.0
    hits = sum(1 for label in expected_labels if label.lower() in text)
    return hits / len(expected_labels)


def propose(dataset: list[dict], prompt: str) -> dict:
    """A candidate prompt. It is not installed into the employee, supervisor, or manager."""
    scores = []
    for row in dataset:
        scores.append(score_example(row.get("labels") or [], row.get("output") or ""))
    mean = sum(scores) / len(scores) if scores else 0.0
    return {
        "prompt": prompt,
        "held_out_score": round(mean, 3),
        "approved": False,
        "status": "CANDIDATE",
        "note": "A person must copy an approved version into the engine. The running process will not.",
    }


def load_dataset(path: str) -> list[dict]:
    file = Path(path)
    if not file.exists():
        return []
    data = json.loads(file.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def dspy_decision() -> dict:
    return {
        "project": "stanfordnlp/dspy",
        "decision": "OPTIONAL",
        "installed": False,
        "reason": "GEPA and MIPROv2 are offline optimisers. Production prompts stay in source control. The package is not imported on the request path.",
    }
