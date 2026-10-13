"""Crash resume, stage deadlines, and the single final evaluation."""

from __future__ import annotations

import os
import time
import uuid
from contextvars import copy_context
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from .run_token import RunToken, StaleWrite, bind, reset

TERMINAL = {"COMPLETED", "FAILED", "APPROVED", "UNRESOLVED"}
WAITING_FOR_LEE = {"AWAITING_CLARIFICATION", "AWAITING_APPROVAL"}
_STAGE_ATTEMPTS: list[dict] = []


def stage_timeout(stage: str) -> float:
    specific = os.environ.get(f"AYVEN_TIMEOUT_{stage.upper()}", "")
    if specific:
        return float(specific)
    return float(os.environ.get("AYVEN_STAGE_TIMEOUT_S", "120"))


def stage_attempts() -> list[dict]:
    return list(_STAGE_ATTEMPTS)


def backoff_seconds(attempt: int) -> float:
    """Increasing wait before retry ``attempt`` (1 = first retry). Capped."""
    base = float(os.environ.get("AYVEN_RETRY_BACKOFF_S", "1"))
    cap = float(os.environ.get("AYVEN_RETRY_BACKOFF_CAP_S", "8"))
    if base < 0:
        base = 0.0
    if cap < 0:
        cap = 0.0
    return min(cap, base * (2 ** max(0, attempt - 1)))


def run_bounded(stage: str, fn, timeout: float | None = None):
    """Run ``fn`` on a worker. On expiry return ``(None, '<stage>_timeout')``.

    The worker's run token is cancelled on expiry, so a late insert or update
    from that thread is rejected.
    """
    limit = stage_timeout(stage) if timeout is None else timeout
    token = RunToken()

    def wrapped():
        handle = bind(token)
        try:
            return fn()
        except StaleWrite:
            return None
        finally:
            reset(handle)

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(copy_context().run, wrapped)
    try:
        result = future.result(timeout=limit), None
        _STAGE_ATTEMPTS.append({"stage": stage, "error": "", "at": _stamp()})
        return result
    except FuturesTimeout:
        token.cancel()
        error = f"{stage}_timeout"
        _STAGE_ATTEMPTS.append({"stage": stage, "error": error, "at": _stamp()})
        return None, error
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def run_retries(fn, *, stage: str = "stage", max_attempts: int | None = None, project_id: str = "", task_id: str = "", needed_from: str = "Lee", independent=None) -> dict:
    """Try ``fn(attempt)`` up to the cap, waiting longer between failures.

    After the cap, record a blocker for the director and an approval for Lee.
    Nothing is sent outside the process. ``independent`` still runs.
    """
    limit = max(1, int(max_attempts if max_attempts is not None else os.environ.get("AYVEN_MAX_ATTEMPTS", "3")))
    attempts: list[dict] = []
    last = ""
    result = None
    for number in range(1, limit + 1):
        waited = 0.0 if number == 1 else backoff_seconds(number - 1)
        if waited:
            time.sleep(waited)
        started = _stamp()
        try:
            result = fn(number)
            failed = isinstance(result, dict) and result.get("ok") is False
            last = str(result.get("error") or "failed") if failed else ""
        except Exception as exc:
            failed = True
            result = None
            last = f"{type(exc).__name__}: {exc}"
        attempts.append({"attempt": number, "at": started, "ok": not failed, "error": last, "backoff_s": waited})
        if not failed:
            if independent is not None:
                independent()
            return {"ok": True, "attempts": attempts, "result": result, "blocker": None, "approval": None, "external_sends": 0}
    reason = f"{stage} failed after {limit} attempts. Last error: {last}. Escalated to the director, then to Lee."
    blocker = None
    approval = None
    if project_id:
        from ..goals import add_blocker

        blocker = add_blocker(project_id, "error", reason, needed_from, package_id=task_id)
        approval = _approval_for_lee(project_id, task_id or project_id, reason)
    if independent is not None:
        independent()
    return {"ok": False, "attempts": attempts, "result": None, "blocker": blocker, "approval": approval, "external_sends": 0}


def resume_unfinished() -> list[str]:
    """Re-dispatch projects left ``running`` when the worker process died.

    ``AYVEN_RESUME=1`` is required. Packages waiting on Lee are left waiting.
    Done work is not submitted again.
    """
    if os.environ.get("AYVEN_RESUME", "0") != "1":
        return []
    from ..db import connect
    from .execution import run_objective

    conn = connect()
    rows = conn.execute(
        "SELECT id, objective FROM projects WHERE status IN ('running', 'queued')"
    ).fetchall()
    conn.close()
    resumed: list[str] = []
    for row in rows:
        parent = resume_parent(row["id"])
        if not parent:
            continue
        state = _workflow(parent)
        if state in WAITING_FOR_LEE:
            continue
        run_objective(row["id"], row["objective"] or "")
        resumed.append(row["id"])
    return resumed


def _workflow(package_id: str) -> str:
    from ..db import connect

    conn = connect()
    row = conn.execute("SELECT workflow_state FROM work_packages WHERE id=?", (package_id,)).fetchone()
    conn.close()
    return ((row["workflow_state"] if row else "") or "").upper()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approval_for_lee(project_id: str, task_id: str, summary: str) -> str:
    from ..db import connect

    approval_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO approvals(id, task_id, project_id, agent_id, summary, status, created_at) VALUES(?,?,?,?,?,?,?)",
        (approval_id, task_id, project_id, "director", summary[:500], "pending", _stamp()),
    )
    conn.commit()
    conn.close()
    return approval_id


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
