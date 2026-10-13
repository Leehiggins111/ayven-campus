"""Generated files live in the database so a restart does not delete them."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from .db import connect


def ensure_schema(conn=None) -> None:
    own = conn is None
    conn = conn or connect()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stored_files (
            id TEXT PRIMARY KEY,
            package_id TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            content BLOB NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(package_id, name)
        )"""
    )
    conn.commit()
    if own:
        conn.close()


def save_bytes(package_id: str, name: str, content: bytes, kind: str = "file") -> dict:
    ensure_schema()
    digest = hashlib.sha256(content).hexdigest()
    conn = connect()
    conn.execute(
        """INSERT INTO stored_files(id, package_id, name, kind, size, sha256, content, created_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(package_id, name) DO UPDATE SET
             kind=excluded.kind, size=excluded.size, sha256=excluded.sha256,
             content=excluded.content, created_at=excluded.created_at""",
        (str(uuid.uuid4()), package_id, name, kind, len(content), digest, content, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return {"name": name, "size": len(content), "sha256": digest, "kind": kind}


def load_bytes(package_id: str, name: str) -> bytes | None:
    ensure_schema()
    conn = connect()
    row = conn.execute(
        "SELECT content FROM stored_files WHERE package_id=? AND name=?",
        (package_id, name),
    ).fetchone()
    conn.close()
    if not row:
        return None
    content = row["content"]
    return bytes(content) if content is not None else None


def list_files(package_id: str) -> list[dict]:
    ensure_schema()
    conn = connect()
    rows = conn.execute(
        "SELECT name, kind, size, sha256 FROM stored_files WHERE package_id=? ORDER BY name",
        (package_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def store_package_files(package_id: str, findings: str, research_json: str = "", observability_json: str = "") -> list[dict]:
    """Save a finished package's generated files so bundle.zip can serve them.

    The report is always saved. Program files are saved only when their tests
    passed (``stored_files``); web files only when the web check verified them.
    """
    import json

    if not package_id or not (findings or "").strip():
        return []
    saved = [save_bytes(package_id, "report.md", findings.encode("utf-8"), "deliverable")]
    from .intelligence.software import stored_files

    for name, source in stored_files(findings).items():
        saved.append(save_bytes(package_id, name, source.encode("utf-8"), "program"))
    try:
        research = json.loads(research_json or "{}")
        obs = json.loads(observability_json or "{}")
        research["web_execution"] = research.get("web_execution") or obs.get("web_execution") or {}
        from .intelligence.web_artifact import artifact_files, verified_web

        web = artifact_files(findings) if verified_web(findings, research) else {}
    except (ValueError, TypeError, AttributeError, KeyError):
        web = {}
    for name, source in web.items():
        saved.append(save_bytes(package_id, name, str(source).encode("utf-8"), "web"))
    return saved
