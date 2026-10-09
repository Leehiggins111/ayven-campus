"""Crash resume, stage deadlines, and the single final evaluation."""

from __future__ import annotations

import os
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

TERMINAL = {"COMPLETED", "FAILED", "APPROVED"}


def stage_timeout(stage: str) -> float:
    specific = os.environ.get(f"AYVEN_TIMEOUT_{stage.upper()}", "")
    if specific:
        return float(specific)
    return float(os.environ.get("AYVEN_STAGE_TIMEOUT_S", "120"))


def run_bounded(stage: str, fn, timeout: float | None = None):
    """Run ``fn`` on a worker. On expiry return ``(None, '<stage>_timeout')``."""
    limit = stage_timeout(stage) if timeout is None else timeout
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(copy_context().run, fn)
    try:
        return future.result(timeout=limit), None
    except FuturesTimeout:
        return None, f"{stage}_timeout"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def resume_parent(project_id: str) -> str:
    """Return an unfinished parent package id, or '' when the run has to start."""
    from ..db import connect

    conn = connect()
    rows = conn.execute(
        "SELECT id, workflow_state FROM work_packages WHERE project_id=? AND parent_id IS NULL ORDER BY updated_at DESC",
        (project_id,),
    ).fetchall()
    conn.close()
    for row in rows:
        state = (row["workflow_state"] or "IN_PROGRESS").upper()
        if state not in TERMINAL:
            return row["id"]
    return ""


def final_evaluation(observability: dict, *, decision: str = "", findings: str = "") -> dict:
    """One concise verdict. Task completion is not the same number as safety."""
    completion = observability.get("completion") or {}
    outcome = completion.get("outcome") or "FAIL"
    if outcome not in ("PASS", "PARTIAL", "FAIL"):
        outcome = "FAIL"
    challenges = observability.get("material_challenges") or []
    unsupported = list(observability.get("unsupported_removed") or [])
    pages = [page for page in (observability.get("pages") or []) if page]
    tools = observability.get("supervisor_tools") or []
    return {
        "overall": outcome,
        "verdict": outcome,
        "task_completion": completion.get("outcome") or "FAIL",
        "safety": completion.get("safety_outcome") or "",
        "grounding": {
            "sources": len(pages),
            "unsupported_claims": len(unsupported),
            "challenged": len(challenges),
        },
        "calculation": (observability.get("calculation") or (observability.get("research") or {}).get("calculation") or {}),
        "supervisor": observability.get("supervisor_decisions") or [],
        "manager": decision or observability.get("manager_decision") or "",
        "manager_effectiveness": observability.get("resolution") or {},
        "unsupported_claims": unsupported,
        "findings_present": bool((findings or "").strip()),
        "stats": {
            "calls": len(observability.get("runtime") or []) or observability.get("model_calls") or 0,
            "tools": len(tools) + len(observability.get("queries") or []),
            "sources": len(pages),
            "claims": len(challenges),
            "retries": observability.get("retries") or 0,
            "latency_s": observability.get("latency_s") or 0,
            "tokens": observability.get("tokens") or 0,
        },
    }
