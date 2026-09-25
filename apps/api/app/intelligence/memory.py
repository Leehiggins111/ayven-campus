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


def _tokens(text: str) -> set[str]:
    return {part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(part) > 2}


def retrieve(query: str, *, scopes: tuple[str, ...] | list[str] | None = None, subject_id: str | None = None, limit: int = 4) -> list[dict]:
    """Rank memories by token overlap. Score 0, expired, and superseded rows stay out."""
    wanted = _tokens(query)
    if not wanted:
        return []
    conn = connect()
    sql = "SELECT * FROM memories WHERE (superseded_by IS NULL OR superseded_by='')"
    args: list = []
    if scopes:
        marks = ",".join("?" * len(tuple(scopes)))
        sql += f" AND scope IN ({marks})"
        args.extend(tuple(scopes))
    if subject_id:
        sql += " AND subject_id=?"
        args.append(subject_id)
    rows = [dict(row) for row in conn.execute(sql, args).fetchall()]
    conn.close()
    ts = now()
    ranked = []
    for row in rows:
        if row.get("expires_at") and row["expires_at"] <= ts:
            continue
        overlap = wanted & _tokens(row.get("content") or "")
        if not overlap:
            continue
        row["score"] = len(overlap)
        ranked.append(row)
    ranked.sort(key=lambda item: (-item["score"], item.get("created_at") or ""), reverse=False)
    return ranked[:limit]


def format_for_prompt(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = ["Relevant memory (context only, not evidence):"]
    for row in rows:
        lines.append(f"- [{row.get('scope')} {row.get('subject_id')}] {row.get('content')} (provenance={row.get('provenance')})")
    return "\n".join(lines)


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
