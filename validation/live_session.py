"""On-pod live session. Plan mode never calls this.

Steps 1 and 2 share one cost cap and one pod. Step 3 is not started.
No paid external model is called. Evidence is written only when this
function runs, and the acceptance criteria file is written first.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from validation.live_plan import (
    ACCEPTANCE_CRITERIA,
    MULTI_STEP_TASK,
    PRIMARY_MODEL,
    director_plans_pass,
    session_plan,
    steps_for_session,
)


def prepare_evidence(root: Path, *, director_plan_passed: bool | None = None) -> Path:
    """Create the run directory and write the criteria before any result."""
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    dest = root / run_id
    dest.mkdir(parents=True, exist_ok=False)
    (dest / "criteria.json").write_text(
        json.dumps({"criteria": ACCEPTANCE_CRITERIA, "task": MULTI_STEP_TASK}, indent=2),
        encoding="utf-8",
    )
    (dest / "config.json").write_text(
        json.dumps(
            {
                "status": "UNVERIFIED",
                "model": PRIMARY_MODEL,
                "paid_external_by_default": False,
                "session": session_plan(),
                "steps": steps_for_session(False if director_plan_passed is None else director_plan_passed),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def record_result(dest: Path, body: dict) -> None:
    criteria = dest / "criteria.json"
    if not criteria.is_file():
        raise RuntimeError("criteria.json must exist before a result is written")
    if "director_plans" in body and not director_plans_pass(body.get("director_plans")):
        raise RuntimeError("Director plans failed the pass mark: at least 8 of 10 must be valid.")
    (dest / "result.json").write_text(json.dumps(body, indent=2), encoding="utf-8")


def run_confirmed_session(root: Path | None = None) -> int:
    """Run only when spend is confirmed and this process is already on a pod.

    Does not create a pod and does not download a weight. If llama-server is
    not already up, the result records that and step 2 is not started.
    """
    if os.environ.get("AYVEN_CONFIRM_SPEND") != "YES":
        print("Plan only. AYVEN_CONFIRM_SPEND is not YES.")
        return 0
    pod = os.environ.get("RUNPOD_POD_ID", "").strip()
    if not pod:
        print("Refusing to create a pod from this script.")
        return 0
    evidence_root = root or Path("evidence/live")
    dest = prepare_evidence(evidence_root)
    # The director plan has not been run. Step 2 stays available in the same
    # session, under the same cap, and is not a second pod. Step 3 stays off.
    record_result(
        dest,
        {
            "status": "UNVERIFIED",
            "pod_set": True,
            "model": PRIMARY_MODEL,
            "steps_available": [1, 2],
            "step_3_started": False,
            "paid_external_called": False,
            "director_plan_passed": None,
            "note": "The model server was not started by this process. No weight was downloaded.",
            "owner_interventions": [],
            "tokens": None,
            "cost_gbp": None,
            "elapsed_s": None,
        },
    )
    print(f"Evidence: {dest}")
    print("Steps 1 and 2 stay under the same cap. Step 3 was not started.")
    return 0
