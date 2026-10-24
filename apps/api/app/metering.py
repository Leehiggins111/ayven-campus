"""One metered path for every workforce model call.

Every call made for a job goes through ``metered_call``: the job cap is checked and
the call is reserved before it runs, its cost is recorded after it ends (also when it
fails or times out), and a completed result is kept by a step key so a crash/restart
replays it instead of calling and charging again.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

CURRENT_JOB: ContextVar[str] = ContextVar("ayven_current_job", default="")

# Project id -> stop message. A builder that swallows exceptions still stops the job.
BUDGET_STOPS: dict[str, str] = {}

# Project id -> message once a job's model calls kept timing out (retries exhausted).
TIMEOUT_STOPS: dict[str, str] = {}
_CONSECUTIVE_TIMEOUTS: dict[str, int] = {}

_OCCURRENCE: dict[tuple[str, str], int] = {}
_LOCK = threading.Lock()


class BudgetExceeded(RuntimeError):
    """The job's own cap would be passed by the next model call. Terminal: never retried."""


class ModelCallTimeout(TimeoutError):
    """A single model call passed its total deadline."""

    def __init__(self, seconds: float):
        super().__init__(f"model call timed out after {seconds:g}s")


@contextmanager
def job_scope(project_id: str):
    token = CURRENT_JOB.set(project_id or "")
    try:
        yield
    finally:
        CURRENT_JOB.reset(token)


def scoped(fn):
    """Run ``fn(project_id, ...)`` with its model calls metered against that job."""
    import functools

    @functools.wraps(fn)
    def wrapper(project_id, *args, **kwargs):
        with job_scope(project_id):
            return fn(project_id, *args, **kwargs)

    return wrapper


def call_deadline() -> float:
    """Total seconds one model call may take, however slowly bytes arrive.

    ``AYVEN_TIMEOUT_MODEL`` or the stage timeout ``AYVEN_STAGE_TIMEOUT_S`` when set;
    otherwise the configured per-request timeout, now applied to the whole call.
    """
    for name in ("AYVEN_TIMEOUT_MODEL", "AYVEN_STAGE_TIMEOUT_S"):
        value = os.environ.get(name, "").strip()
        if value:
            return float(value)
    base = os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "")
    return float(os.environ.get("AYVEN_LLM_TIMEOUT_S", "600" if ":11434" in base else "90"))


def _ensure_cache() -> None:
    from .db import connect

    conn = connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS model_call_cache(
            job_id TEXT NOT NULL, step_key TEXT NOT NULL, occurrence INTEGER NOT NULL,
            text TEXT, tokens INTEGER, meta_json TEXT, created_at REAL,
            PRIMARY KEY(job_id, step_key, occurrence))"""
    )
    conn.commit()
    conn.close()


def _step_key(role: str, system: str, user: str, max_tokens: int, extra) -> str:
    raw = json.dumps([role.upper(), system, user, int(max_tokens), extra], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached(job_id: str, key: str, occurrence: int):
    from .db import connect

    conn = connect()
    row = conn.execute(
        "SELECT text, tokens, meta_json FROM model_call_cache WHERE job_id=? AND step_key=? AND occurrence=?",
        (job_id, key, occurrence),
    ).fetchone()
    conn.close()
    if not row:
        return None
    meta = json.loads(row["meta_json"] or "{}")
    meta["replayed"] = True
    return row["text"], int(row["tokens"] or 0), meta


def _store(job_id: str, key: str, occurrence: int, text, tokens, meta) -> None:
    from .db import connect

    try:
        conn = connect()
        conn.execute(
            "INSERT OR REPLACE INTO model_call_cache(job_id, step_key, occurrence, text, tokens, meta_json, created_at) VALUES(?,?,?,?,?,?,?)",
            (job_id, key, occurrence, text if isinstance(text, str) else "", int(tokens or 0),
             json.dumps(meta or {}, default=str), time.time()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def timeout_limit() -> int:
    return max(1, int(os.environ.get("AYVEN_MODEL_TIMEOUTS_BEFORE_STOP", "3")))


def raise_if_stopped(job_id: str) -> None:
    """Called after each stage: a swallowed budget or timeout stop still ends the job."""
    if job_id in BUDGET_STOPS:
        raise BudgetExceeded(BUDGET_STOPS[job_id])
    if job_id in TIMEOUT_STOPS:
        raise ModelCallTimeout(float(TIMEOUT_STOPS[job_id]))


def forget_job(job_id: str) -> None:
    """A finished or failed job's cache is dropped, so a retry calls the model again."""
    BUDGET_STOPS.pop(job_id, None)
    TIMEOUT_STOPS.pop(job_id, None)
    _CONSECUTIVE_TIMEOUTS.pop(job_id, None)
    with _LOCK:
        for item in [k for k in _OCCURRENCE if k[0] == job_id]:
            _OCCURRENCE.pop(item, None)
    try:
        from .db import connect

        _ensure_cache()
        conn = connect()
        conn.execute("DELETE FROM model_call_cache WHERE job_id=?", (job_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _cap_usd(job_id: str):
    try:
        from .goals import get_goal

        value = (get_goal(job_id) or {}).get("job_cap_usd")
        return float(value) if value is not None else None
    except Exception:
        return None


def metered_call(role: str, system: str, user: str, max_tokens: int, call, extra=None):
    """Run ``call()`` -> (text, tokens, meta) under the current job's cap and ledger."""
    job_id = CURRENT_JOB.get()
    if not job_id or (role or "").upper() == "ESCALATION":
        return call()
    from .intelligence.budget import check_and_reserve, commit, effective_job_cap_usd, estimate_usd, gbp

    raise_if_stopped(job_id)
    _ensure_cache()
    key = _step_key(role, system, user, max_tokens, extra)
    with _LOCK:
        occurrence = _OCCURRENCE.get((job_id, key), 0)
        _OCCURRENCE[(job_id, key)] = occurrence + 1
    hit = _cached(job_id, key, occurrence)
    if hit is not None:
        return hit  # completed before a restart: replayed, not called or charged again
    cap = _cap_usd(job_id)
    held = check_and_reserve(job_id, role, max_tokens, job_cap_usd=cap)
    if not held.get("ok"):
        if held.get("status") == "BUDGET_EXCEEDED":
            message = f"this job's £{gbp(effective_job_cap_usd(cap)):.2f} budget would be passed by the next model call"
            BUDGET_STOPS[job_id] = message
            raise BudgetExceeded(message)
        raise BudgetExceeded(str(held.get("reason") or held.get("status") or "budget refused"))
    reservation = held.get("reservation_id")
    started = time.monotonic()
    try:
        text, tokens, meta = call()
    except BaseException as exc:
        if isinstance(exc, ModelCallTimeout):
            count = _CONSECUTIVE_TIMEOUTS.get(job_id, 0) + 1
            _CONSECUTIVE_TIMEOUTS[job_id] = count
            if count >= timeout_limit():
                TIMEOUT_STOPS[job_id] = f"{call_deadline():g}"
        # The GPU time was used even though the call failed: charge the time it took.
        try:
            commit(reservation, max_tokens, 0, amount_usd=estimate_usd(role, seconds=time.monotonic() - started))
        except Exception:
            pass
        raise
    _CONSECUTIVE_TIMEOUTS.pop(job_id, None)
    meta = dict(meta or {})
    prompt_tokens = int(meta.get("prompt_tokens") or len((system or "") + (user or "")) // 4)
    completion_tokens = int(meta.get("completion_tokens") or tokens or 0)
    try:
        commit(reservation, prompt_tokens, completion_tokens,
               amount_usd=estimate_usd(role, input_tokens=prompt_tokens, output_tokens=completion_tokens))
    except Exception:
        pass
    _store(job_id, key, occurrence, text, tokens, meta)
    return text, tokens, meta
