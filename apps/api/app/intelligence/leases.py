"""One claim and one side effect for a job.

DBOS (open PRs #2 and #5, already on this branch) keeps the stage
checkpoints. This module is the piece those PRs left open: two workers
must not run the same job, a superseded attempt must not commit, and a
recorded tool call must not be sent again.

Claims use local SQLite ``BEGIN IMMEDIATE``. They do not use Turso
concurrent writes. A remote ``AYVEN_DB_URL`` is not the claim database.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextvars import ContextVar

from .run_token import StaleWrite

_lease: ContextVar[dict | None] = ContextVar("ayven_lease", default=None)


def lease_seconds() -> int:
    raw = os.environ.get("AYVEN_LEASE_S", "30")
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


def _now() -> int:
    return int(time.time())


def current() -> dict | None:
    return _lease.get()


def bind(job_id: str, owner: str, fence: int):
    return _lease.set({"job_id": job_id, "owner": owner, "fence": int(fence)})


def reset(token) -> None:
    _lease.reset(token)


def _field(row, key: str, index: int):
    if row is None:
        return None
    if isinstance(row, dict):
        return row[key]
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return row[index]


def assert_fence(connection) -> None:
    """Refuse the commit when this attempt is no longer the owner."""
    held = _lease.get()
    if not held:
        return
    row = connection.execute(
        "SELECT owner, fence, state FROM job_leases WHERE job_id=?",
        (held["job_id"],),
    ).fetchone()
    owner = _field(row, "owner", 0)
    fence = _field(row, "fence", 1)
    state = _field(row, "state", 2)
    if row is None or owner != held["owner"] or int(fence) != int(held["fence"]) or state != "running":
        raise StaleWrite("fence superseded at commit")


def claim(job_id: str, owner: str, *, now: int | None = None) -> int | None:
    """Return the new fence, or None when another owner holds the job or retries are spent."""
    from ..db import open_raw

    stamp = _now() if now is None else int(now)
    until = stamp + lease_seconds()
    conn = open_raw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT OR IGNORE INTO job_leases(job_id, owner, lease_until, fence, attempts, max_attempts, state)
               VALUES(?, '', 0, 0, 0, 3, 'ready')""",
            (job_id,),
        )
        row = conn.execute(
            """UPDATE job_leases
               SET owner=?, lease_until=?, fence=fence+1, attempts=attempts+1, state='running'
               WHERE job_id=?
                 AND state IN ('ready', 'running')
                 AND (state='ready' OR lease_until < ?)
                 AND attempts < max_attempts
               RETURNING fence""",
            (owner, until, job_id, stamp),
        ).fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if row is None:
        return None
    return int(_field(row, "fence", 0))


def heartbeat(job_id: str, owner: str, fence: int, *, now: int | None = None) -> bool:
    from ..db import open_raw

    stamp = _now() if now is None else int(now)
    conn = open_raw()
    try:
        cur = conn.execute(
            """UPDATE job_leases SET lease_until=?
               WHERE job_id=? AND owner=? AND fence=? AND state='running'""",
            (stamp + lease_seconds(), job_id, owner, int(fence)),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def finish(job_id: str, owner: str, fence: int) -> None:
    from ..db import open_raw

    conn = open_raw()
    try:
        conn.execute(
            """UPDATE job_leases SET state='done'
               WHERE job_id=? AND owner=? AND fence=? AND state='running'""",
            (job_id, owner, int(fence)),
        )
        conn.commit()
    finally:
        conn.close()


def escalate_expired(*, now: int | None = None) -> list[str]:
    """Mark a dead lease escalated once its attempts are spent. Milo can read that state."""
    from ..db import open_raw

    stamp = _now() if now is None else int(now)
    conn = open_raw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """UPDATE job_leases SET state='escalated'
               WHERE state='running' AND lease_until < ? AND attempts >= max_attempts
               RETURNING job_id""",
            (stamp,),
        ).fetchall()
        ids = [str(_field(row, "job_id", 0)) for row in rows]
        for job_id in ids:
            conn.execute(
                """UPDATE projects SET status='waiting', result=?
                   WHERE id=? AND status IN ('running', 'queued')""",
                ("Escalated: the lease expired and the attempt limit was reached.", job_id),
            )
        conn.commit()
        return ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def lease_view(job_id: str) -> dict | None:
    from ..db import open_raw

    conn = open_raw()
    try:
        row = conn.execute(
            "SELECT job_id, owner, fence, attempts, max_attempts, state, lease_until FROM job_leases WHERE job_id=?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "job_id": _field(row, "job_id", 0),
        "owner": _field(row, "owner", 1),
        "fence": int(_field(row, "fence", 2)),
        "attempts": int(_field(row, "attempts", 3)),
        "max_attempts": int(_field(row, "max_attempts", 4)),
        "state": _field(row, "state", 5),
        "lease_until": int(_field(row, "lease_until", 6)),
    }


def _idem_key(job_id: str, stage: str, action: str, target: str) -> str:
    raw = f"{job_id}|{stage}|{action}|{target}"
    return hashlib.sha256(raw.encode()).hexdigest()


def perform_side_effect(job_id: str, stage: str, action: str, target: str, fn):
    """Run ``fn`` once. A second call returns the stored result and does not call ``fn``.

    The pending row is committed before ``fn``. A replay therefore does not
    send the action again, including when the first attempt died before it
    could store a result.
    """
    from ..db import open_raw

    key = _idem_key(job_id, stage, action, target)
    held = _lease.get() or {}
    fence = int(held.get("fence") or 0)
    conn = open_raw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            """INSERT INTO side_effects(idem_key, job_id, fence, stage, action, target, status, result_json, created_at)
               VALUES(?,?,?,?,?,?, 'pending', NULL, ?)
               ON CONFLICT(idem_key) DO NOTHING
               RETURNING idem_key""",
            (key, job_id, fence, stage, action, target, _now()),
        ).fetchone()
        if inserted is None:
            existing = conn.execute(
                "SELECT status, result_json FROM side_effects WHERE idem_key=?",
                (key,),
            ).fetchone()
            assert_fence(conn)
            conn.commit()
            payload = _field(existing, "result_json", 1) if existing else None
            return json.loads(payload) if payload else None
        assert_fence(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        result = fn()
        status = "done"
        stored = json.dumps(result)
    except Exception as exc:
        status = "failed"
        stored = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        conn = open_raw()
        try:
            conn.execute(
                "UPDATE side_effects SET status=?, result_json=? WHERE idem_key=?",
                (status, stored, key),
            )
            conn.commit()
        finally:
            conn.close()
        raise
    conn = open_raw()
    try:
        conn.execute(
            "UPDATE side_effects SET status=?, result_json=? WHERE idem_key=?",
            (status, stored, key),
        )
        conn.commit()
    finally:
        conn.close()
    return result
