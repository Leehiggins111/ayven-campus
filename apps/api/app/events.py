from __future__ import annotations

import json
import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import connect

_subscribers: list[queue.Queue] = []
_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def emit(
    type_: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    department_id: str | None = None,
    status: str | None = None,
    tool: str | None = None,
    summary: str = "",
    progress: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": now_iso(),
        "project_id": project_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "department_id": department_id,
        "type": type_,
        "status": status,
        "tool": tool,
        "summary": summary,
        "progress": progress,
        "metadata": metadata or {},
    }
    conn = connect()
    conn.execute(
        """INSERT INTO events(event_id,timestamp,project_id,task_id,agent_id,
           department_id,type,status,tool,summary,progress,metadata)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event["event_id"],
            event["timestamp"],
            project_id,
            task_id,
            agent_id,
            department_id,
            type_,
            status,
            tool,
            summary,
            progress,
            json.dumps(event["metadata"]),
        ),
    )
    conn.commit()
    conn.close()
    with _lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)
    return event
