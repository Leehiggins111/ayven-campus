"""Runs the independent acceptance harness (validation/acceptance_journey.py) in SIMULATED mode.

The harness starts Campus in its own processes on a temporary database: one process runs
the journeys, a second re-reads every job after a restart, and a third does so after the
local workflow store is wiped, as a new hosted instance would see it. Its JSON report is
kept at AYVEN_ACCEPTANCE_REPORT when set (CI uploads it). Hosted evidence is never produced here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_the_shared_journey_passes_in_simulated_mode(tmp_path):
    root = Path(__file__).resolve().parents[3]
    report_path = Path(os.environ.get("AYVEN_ACCEPTANCE_REPORT") or (tmp_path / "acceptance.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(root / "validation" / "acceptance_journey.py"), "--report", str(report_path)],
        cwd=str(root), capture_output=True, text=True, timeout=1500,
    )
    assert report_path.exists(), (proc.stdout + proc.stderr)[-4000:]
    report = json.loads(report_path.read_text())
    assert report["evidence"] == "SIMULATED"
    failures = [step for step in report["steps"] if step.get("passed") is not True]
    assert not failures, json.dumps(failures, indent=2)[:6000]
    phases = {step["journey"].split(":")[0] for step in report["steps"]}
    assert "restart" in phases and "new instance (local workflow store wiped)" in phases, phases
    assert proc.returncode == 0, proc.stdout[-3000:]
