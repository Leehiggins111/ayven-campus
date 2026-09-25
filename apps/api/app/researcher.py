"""Single-package fallback. Uses the same intelligence loop as the workforce."""

from __future__ import annotations

from .db import connect


def run_package(package_id: str) -> None:
    conn = connect()
    pkg = conn.execute("SELECT * FROM work_packages WHERE id=?", (package_id,)).fetchone()
    conn.close()
    if not pkg:
        return
    from .intelligence.execution import run_objective

    run_objective(pkg["project_id"], pkg["objective"], pkg["task_id"])
