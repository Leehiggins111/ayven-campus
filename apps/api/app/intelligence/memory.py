"""Scoped memory. Stores conclusions with provenance, not raw transcripts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..db import connect
from .think import strip_think

SCOPES = ("WORK_PACKAGE", "PROJECT", "CUSTOMER", "DOMAIN", "COMPANY")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def remember(
    scope: str,
    subject_id: str,
    content: str,
    *,
    provenance: str,
    source_url: str = "",
    tags: str = "",
    expires_at: str | None = None,
    supersedes: str | None = None,
) -> str:
    if scope not in SCOPES:
        raise ValueError(f"unknown memory scope {scope}")
    text = strip_think(content)
    if not text:
        return ""
    mem_id = str(uuid.uuid4())
    conn = connect()
    if supersedes:
        conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (mem_id, supersedes))
    conn.execute(
        """INSERT INTO memories(
            id,namespace,content,created_at,scope,subject_id,provenance,source_url,expires_at,superseded_by,tags
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (mem_id, scope, text[:2000], now(), scope, subject_id, provenance[:300], source_url, expires_at, "", tags[:200]),
    )
    conn.commit()
    conn.close()
    return mem_id


def search(scope: str, query: str, subject_id: str | None = None, limit: int = 8) -> list[dict]:
    conn = connect()
    sql = "SELECT * FROM memories WHERE scope=? AND (superseded_by IS NULL OR superseded_by='') AND content LIKE ?"
    args: list = [scope, f"%{query[:80]}%"]
    if subject_id:
        sql += " AND subject_id=?"
        args.append(subject_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    ts = now()
    return [row for row in rows if not row.get("expires_at") or row["expires_at"] > ts]
