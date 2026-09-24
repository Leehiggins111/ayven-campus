from __future__ import annotations

from datetime import datetime, timezone

from . import events
from .db import connect


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_package(pid: str, **fields) -> None:
    fields["updated_at"] = now()
    conn = connect()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE work_packages SET {sets} WHERE id=?", [*fields.values(), pid])
    conn.commit()
    conn.close()


def route(package_id: str) -> str:
    conn = connect()
    row = conn.execute("SELECT * FROM work_packages WHERE id=?", (package_id,)).fetchone()
    conn.close()
    if not row:
        return "missing"
    needs = int(row["requires_approval"] or 0) == 1
    dest = "approval" if needs else "command"
    stage = "approval" if needs else "results"
    status = "needs_approval" if needs else "complete"
    update_package(package_id, stage=stage, destination=dest, status=status)
    events.emit(
        "package.routed",
        project_id=row["project_id"],
        task_id=row["task_id"],
        agent_id="distribution",
        department_id="command",
        status=status,
        summary=f"Distribution routed package to {dest}",
    )
    if needs:
        events.emit(
            "package.waiting_approval",
            project_id=row["project_id"],
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            department_id="command",
            status="needs_approval",
            summary="Package waiting in approval bay",
        )
    else:
        events.emit(
            "package.delivered",
            project_id=row["project_id"],
            task_id=row["task_id"],
            agent_id="milo",
            department_id="command",
            status="complete",
            summary="Package delivered to Milo / Results",
        )
    return dest
