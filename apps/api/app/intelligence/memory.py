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
    confidence: float = 0.7,
) -> str:
    if scope not in SCOPES:
        raise ValueError(f"unknown memory scope {scope}")
    from .boundary import reject_reasoning_query, separate_channels

    text = separate_channels(strip_think(content)).executable
    if not text or reject_reasoning_query(text):
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
    try:
        conn.execute(
            "UPDATE memories SET confidence=?, last_used=?, supersedes=? WHERE id=?",
            (confidence, now(), supersedes or "", mem_id),
        )
    except Exception:
        pass
    conn.commit()
    conn.close()
    return mem_id


def _tokens(text: str) -> set[str]:
    return {part for part in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(part) > 2}


_EMBEDDER = {"model": None, "error": ""}


def _embed(texts: list[str]) -> list[list[float]] | None:
    """Local fastembed vectors. A failure falls back to lexical overlap."""
    if _EMBEDDER["error"] == "disabled":
        return None
    try:
        if _EMBEDDER["model"] is None and not _EMBEDDER["error"]:
            from fastembed import TextEmbedding

            _EMBEDDER["model"] = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        model = _EMBEDDER["model"]
        if model is None:
            return None
        return [list(float(value) for value in vector) for vector in model.embed(texts)]
    except Exception as exc:
        _EMBEDDER["error"] = f"{type(exc).__name__}: {exc}"
        return None


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def retrieve(
    query: str,
    *,
    scopes: tuple[str, ...] | list[str] | None = None,
    subject_id: str | None = None,
    limit: int = 4,
    threshold: float = 1,
    semantic_threshold: float = 0.55,
    budget_chars: int = 1200,
) -> list[dict]:
    """Semantic rank when a local embedder loads, otherwise token overlap.

    Expired and superseded rows stay out. The context budget drops the tail.
    """
    wanted = _tokens(query)
    if not wanted and not query.strip():
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
    live = [row for row in rows if not row.get("expires_at") or row["expires_at"] > ts]
    vectors = _embed([query, *[row.get("content") or "" for row in live]]) if live else None
    ranked = []
    lexical_floor = int(threshold) if threshold >= 1 else 1
    for index, row in enumerate(live):
        overlap = wanted & _tokens(row.get("content") or "")
        semantic = _cosine(vectors[0], vectors[index + 1]) if vectors else 0.0
        lexical_ok = len(overlap) >= lexical_floor
        semantic_ok = bool(vectors) and semantic >= semantic_threshold
        if not lexical_ok and not semantic_ok:
            continue
        row["score"] = round(semantic, 4) if semantic_ok else len(overlap)
        row["semantic_score"] = round(semantic, 4)
        row["retrieval"] = "semantic" if semantic_ok else "lexical"
        row["last_used"] = ts
        ranked.append(row)
    ranked.sort(key=lambda item: (-(item.get("semantic_score") or 0), -(item["score"] if isinstance(item["score"], int) else 0), item.get("created_at") or ""))
    kept = []
    used = 0
    for row in ranked:
        text = row.get("content") or ""
        if len(text) > budget_chars or used + len(text) > budget_chars:
            continue
        used += len(text)
        kept.append(row)
        if len(kept) >= limit:
            break
    return kept


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
