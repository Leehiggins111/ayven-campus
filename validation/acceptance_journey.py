"""Independent acceptance harness for the shared Milo -> Campus journey.

Two kinds of evidence, never mixed:

  SIMULATED  (default)  Campus runs in a child process on a temporary SQLite database with a
                        deterministic role generator standing in for every model. Then a second,
                        fresh process opens the same database and re-checks every job: that is
                        the restart test. Deterministic; safe for CI.

  HOSTED     (--hosted URL)  Talks to a deployed Campus over HTTPS. Read-only unless
                        --allow-writes is given, because a write creates real rows in that
                        service's database. Hosted results are reported as HOSTED and are never
                        merged with simulated ones.

Usage:
  python validation/acceptance_journey.py --report out.json
  AYVEN_ACCEPTANCE_KEY=... python validation/acceptance_journey.py --hosted https://... --report hosted.json
  python validation/acceptance_journey.py --hosted https://... --job <project_id>   # read one job

Exit code 0 only when every step that ran passed. Nothing here spends money or deploys.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
AUTH_KEY = "acceptance-harness-key-with-at-least-32-characters"


# --- step recording ------------------------------------------------------------------------------

class Journey:
    def __init__(self, name: str, report: dict):
        self.name = name
        self.report = report
        self.failed = False

    def step(self, name: str, fn):
        """Run one step. A failed step skips the rest of this journey, never the next one."""
        record = {"journey": self.name, "step": name}
        if self.failed:
            record.update(passed=None, skipped=True, evidence={"why": "an earlier step in this journey failed"})
            self.report["steps"].append(record)
            return None
        try:
            evidence = fn() or {}
            record.update(passed=True, evidence=evidence)
        except AssertionError as exc:
            self.failed = True
            record.update(passed=False, evidence={"assertion": str(exc)[:1500]})
        except Exception as exc:  # an error is a failure, with its type
            self.failed = True
            record.update(passed=False, evidence={"error": f"{type(exc).__name__}: {exc}"[:1500]})
        self.report["steps"].append(record)
        return record.get("evidence")


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _zip_names(content: bytes) -> list[str]:
    return sorted(zipfile.ZipFile(io.BytesIO(content)).namelist())


# --- simulated providers ---------------------------------------------------------------------------

GOOD_PROGRAM = """FILE: main.py
def add(left, right):
    return left + right

FILE: test_main.py
import main
assert main.add(4, 7) == 11
assert main.add(0, 0) == 0

LAUNCH: python test_main.py
"""
BAD_PROGRAM = GOOD_PROGRAM.replace("return left + right", "return left")
PROGRAM = {"source": GOOD_PROGRAM}


def role_generator(role, system, user, max_tokens):
    """Every model role, answered deterministically. The program text is chosen per job."""
    if role == "MANAGER":
        return "SYNTHESISE\nRationale: the files are the deliverable", 3, {"backend": "acceptance-sim"}
    if role == "SUPERVISOR":
        return "ACCEPT\nNothing was sent.", 2, {"backend": "acceptance-sim"}
    if "Write the test file only" in user or "Write main.py only" in user:
        return PROGRAM["source"], 40, {"backend": "acceptance-sim"}
    if "example_output" in user:
        return json.dumps({"function": "add", "parameters": ["left", "right"], "example_input": [4, 7], "example_output": 11}), 8, {"backend": "acceptance-sim"}
    return "Nothing was sent.", 2, {"backend": "acceptance-sim"}


def simulated_env(db_path: Path) -> dict:
    env = {**os.environ}
    for key in ("AYVEN_WORKFLOW_DATABASE_URL", "AYVEN_DB_URL", "AYVEN_DB_AUTH_TOKEN", "AYVEN_LLM_API_KEY"):
        env.pop(key, None)
    env.update({
        "PYTHONPATH": f"{ROOT}{os.pathsep}{API}",
        "AYVEN_ENV": "local",
        "AYVEN_DURABLE": "1",
        "AYVEN_DB": str(db_path),
        "AYVEN_LLM_STUB": "0",
        "AYVEN_LOCAL_LLM_BASE_URL": "http://127.0.0.1:9/v1",  # never reached: the generator answers
        "AYVEN_AGENT_RUNTIME": "native",
        "AYVEN_USE_QWEN_AGENT": "0",
        "AYVEN_ALLOW_CODE": "1",
        "AYVEN_RESEARCH_MODE": "fixtures",
        "AYVEN_ALLOW_ESCALATION": "0",
        "AYVEN_RETRY_BACKOFF_S": "0",
        "AYVEN_JOB_BUDGET_USD": "2.00",
        "AYVEN_API_TOKEN": AUTH_KEY,
    })
    return env


# --- the journeys (run inside the Campus child process) ----------------------------------------------

def _wait(client, auth, pid: str, until, seconds: float = 120.0) -> dict:
    deadline = time.time() + seconds
    status = {}
    while time.time() < deadline:
        status = client.get(f"/milo/jobs/{pid}", headers=auth).json()
        if until(status):
            return status
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting; last status {json.dumps(status)[:800]}")


def _settled(status: dict) -> bool:
    return status.get("status") not in ("running", "queued")


def run_simulated_journeys(snapshot_path: Path) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import set_role_generator

    report = {"phase": "journeys", "steps": []}
    auth = {"Authorization": f"Bearer {AUTH_KEY}"}
    snapshot: dict[str, dict] = {}
    set_role_generator(role_generator)
    try:
        with TestClient(app) as client:
            _journey_approve(client, auth, report, snapshot)
            _journey_reject(client, auth, report, snapshot)
            _journey_failed_step(client, auth, report, snapshot)
            _journey_clarification(client, auth, report, snapshot)
            _journey_budgets(client, auth, report)
            # What a fresh process must see after the restart.
            for name, job in snapshot.items():
                job.update(_observe(client, auth, job["project_id"]))
    finally:
        set_role_generator(None)
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    return report


def _observe(client, auth, pid: str) -> dict:
    status = client.get(f"/milo/jobs/{pid}", headers=auth).json()
    package = status.get("package_id") or ""
    deliverable = client.get(f"/work-packages/{package}/deliverable.md", headers=auth) if package else None
    bundle = client.get(f"/work-packages/{package}/bundle.zip", headers=auth) if package else None
    return {
        "package_id": package,
        "workflow_state": status.get("workflow_state"),
        "outcome_state": (status.get("outcome") or {}).get("state"),
        "outcome_label": (status.get("outcome") or {}).get("label"),
        "criteria": status.get("criteria_results"),
        "criteria_evidence_sha": _sha(json.dumps(status.get("criteria_evidence"), sort_keys=True)),
        "deliverable_status": deliverable.status_code if deliverable is not None else None,
        "deliverable_sha": _sha(deliverable.content) if deliverable is not None and deliverable.status_code == 200 else None,
        "bundle_status": bundle.status_code if bundle is not None else None,
        "bundle_names": _zip_names(bundle.content) if bundle is not None and bundle.status_code == 200 else None,
        "job_cap_gbp": status.get("job_cap_gbp"),
    }


def _submit(client, auth, body: dict, key: str = "") -> dict:
    headers = {**auth, **({"Idempotency-Key": key} if key else {})}
    response = client.post("/milo/jobs", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _journey_approve(client, auth, report, snapshot):
    j = Journey("brief -> draft -> approval gate -> accepted deliverable", report)
    ctx: dict = {}
    PROGRAM["source"] = GOOD_PROGRAM
    brief = {"objective": "Write a small Python program that adds 4 and 7 and prints the sum.",
             # The first check is met by the program; the second names a figure it never states.
             "acceptance_criteria": ["Adds 4 and 7", "Quotes £25 per visit"], "job_cap_gbp": 1.25}
    key = "acceptance-approve-" + uuid.uuid4().hex

    def submit():
        ctx["job"] = _submit(client, auth, brief, key)
        assert ctx["job"]["duplicate"] is False and ctx["job"]["mode"] in ("SIMULATED", "PARTLY_SIMULATED"), ctx["job"]
        return {"project_id": ctx["job"]["project_id"], "mode": ctx["job"]["mode"]}

    def replay():
        again = _submit(client, auth, brief, key)
        assert again["duplicate"] is True and again["project_id"] == ctx["job"]["project_id"], again
        return {"duplicate": True}

    def draft():
        status = _wait(client, auth, ctx["job"]["project_id"], _settled)
        ctx["status"] = status
        outcome = status["outcome"]
        assert status["workflow_state"] == "AWAITING_APPROVAL", status
        assert outcome["state"] == "NEEDS_APPROVAL" and outcome["simulated"] is True, outcome
        assert status["job_cap_gbp"] == 1.25, status  # kept exactly as sent
        shown = client.get(f"/work-packages/{status['package_id']}/deliverable.md", headers=auth)
        assert shown.status_code == 200 and shown.text.startswith("> DRAFT FOR REVIEW."), shown.text[:200]
        return {"outcome": outcome["label"], "criteria": status["criteria_results"]}

    def evidence():
        rows = ctx["status"]["criteria_evidence"]
        assert len(rows) == 2 and all(row["evaluated"] for row in rows), rows
        assert rows[0]["status"] == "met" and rows[0]["evidence"]["missing"] == [], rows
        assert rows[1]["status"] == "not_met" and "£25" in rows[1]["evidence"]["missing"], rows
        return {"criteria_evidence": rows}

    def gate():
        approval = ctx["status"]["approval"]
        refused = client.post(f"/approvals/{approval['id']}/resolve", json={"decision": "approved"}, headers=auth)
        assert refused.status_code == 409, refused.text
        unmet = [row["criterion"] for row in refused.json()["detail"]["unmet_criteria"]]
        assert unmet == ["Quotes £25 per visit"], unmet
        still = client.get(f"/milo/jobs/{ctx['job']['project_id']}", headers=auth).json()
        assert still["workflow_state"] == "AWAITING_APPROVAL", still
        return {"refused": refused.json()["detail"]["message"]}

    def override():
        approval = ctx["status"]["approval"]
        ok = client.post(f"/approvals/{approval['id']}/resolve",
                         json={"decision": "approved", "reason": "Pricing is out of scope for this program.", "accept_unmet_criteria": True},
                         headers=auth)
        assert ok.status_code == 200, ok.text
        status = _wait(client, auth, ctx["job"]["project_id"], lambda s: s.get("status") == "complete")
        outcome = status["outcome"]
        assert outcome["state"] == "COMPLETED" and outcome["criteria_overridden"] is True, outcome
        assert "SIMULATED" in outcome["label"] and "UNMET CRITERIA ACCEPTED BY LEE" in outcome["label"], outcome
        ctx["status"] = status
        return {"outcome": outcome["label"]}

    def downloads():
        package = ctx["status"]["package_id"]
        shown = client.get(f"/work-packages/{package}/deliverable.md", headers=auth)
        assert shown.headers["x-ayven-outcome"] == "COMPLETED", shown.headers
        assert "> ACCEPTED WITH UNMET CRITERIA." in shown.text and "> SIMULATED." in shown.text, shown.text[:300]
        bundle = client.get(f"/work-packages/{package}/bundle.zip", headers=auth)
        assert bundle.status_code == 200 and _zip_names(bundle.content) == ["main.py", "report.md", "test_main.py"]
        assert "SIMULATED" in bundle.headers["content-disposition"], bundle.headers
        main = client.get(f"/work-packages/{package}/files/main.py", headers=auth)
        assert main.status_code == 200 and "return left + right" in main.text
        return {"bundle": _zip_names(bundle.content), "filename": bundle.headers["content-disposition"]}

    for name, fn in (("submit brief with criteria and a £ budget", submit), ("replay is idempotent", replay),
                     ("draft is labelled and budget kept", draft), ("acceptance checks carry evidence", evidence),
                     ("plain approval with an unmet check is refused", gate),
                     ("explicit override completes and is labelled", override), ("accepted downloads", downloads)):
        j.step(name, fn)
    if "job" in ctx:
        snapshot["approve"] = {"project_id": ctx["job"]["project_id"]}


def _journey_reject(client, auth, report, snapshot):
    j = Journey("draft -> rejected by Lee -> diagnostic only", report)
    ctx: dict = {}
    PROGRAM["source"] = GOOD_PROGRAM

    def submit():
        ctx["job"] = _submit(client, auth, {"objective": "Write a small Python program that adds 4 and 7 and prints the sum. Reject case."})
        ctx["status"] = _wait(client, auth, ctx["job"]["project_id"], _settled)
        assert ctx["status"]["outcome"]["state"] == "NEEDS_APPROVAL", ctx["status"]
        return {"project_id": ctx["job"]["project_id"]}

    def reject_needs_reason():
        approval = ctx["status"]["approval"]
        bare = client.post(f"/approvals/{approval['id']}/resolve", json={"decision": "rejected"}, headers=auth)
        assert bare.status_code == 400, bare.text
        return {"refused": bare.json()["detail"]}

    def reject():
        approval = ctx["status"]["approval"]
        done = client.post(f"/approvals/{approval['id']}/resolve", json={"decision": "rejected", "reason": "Name the function sum_two."}, headers=auth)
        assert done.status_code == 200, done.text
        status = _wait(client, auth, ctx["job"]["project_id"], lambda s: s.get("workflow_state") == "FAILED")
        assert status["outcome"]["state"] == "REJECTED" and "sum_two" in status["outcome"]["reason"], status["outcome"]
        assert status["result"].startswith("REJECTED. NOT A DELIVERABLE."), status["result"][:200]
        ctx["status"] = status
        return {"outcome": status["outcome"]["label"]}

    def not_a_deliverable():
        package = ctx["status"]["package_id"]
        shown = client.get(f"/work-packages/{package}/deliverable.md", headers=auth)
        assert shown.text.startswith("> REJECTED. NOT A DELIVERABLE."), shown.text[:200]
        assert "diagnostic" in shown.headers["content-disposition"], shown.headers
        assert client.get(f"/work-packages/{package}/files/main.py", headers=auth).status_code == 409
        bundle = client.get(f"/work-packages/{package}/bundle.zip", headers=auth)
        assert _zip_names(bundle.content) == ["report.md"]
        report_md = zipfile.ZipFile(io.BytesIO(bundle.content)).read("report.md").decode()
        assert report_md.startswith("# REJECTED. NOT A DELIVERABLE."), report_md[:200]
        accept = client.post(f"/work-packages/{package}/accept", headers=auth)
        assert accept.status_code == 409, accept.text
        return {"bundle": ["report.md"], "accept": accept.status_code}

    for name, fn in (("draft reaches approval", submit), ("rejection needs a reason", reject_needs_reason),
                     ("rejection is recorded as REJECTED", reject), ("rejected work is never a deliverable", not_a_deliverable)):
        j.step(name, fn)
    if "job" in ctx:
        snapshot["reject"] = {"project_id": ctx["job"]["project_id"]}


def _journey_failed_step(client, auth, report, snapshot):
    j = Journey("failed step -> FAILED, never offered for approval", report)
    ctx: dict = {}

    def submit():
        PROGRAM["source"] = BAD_PROGRAM
        try:
            ctx["job"] = _submit(client, auth, {"objective": "Write a small Python program that adds 4 and 7 and prints the sum. Failing case.",
                                                "acceptance_criteria": ["Adds 4 and 7"]})
            ctx["status"] = _wait(client, auth, ctx["job"]["project_id"], _settled)
        finally:
            PROGRAM["source"] = GOOD_PROGRAM
        status = ctx["status"]
        assert status["workflow_state"] == "FAILED" and status["approval"] is None, status
        assert status["outcome"]["state"] == "FAILED", status["outcome"]
        return {"outcome": status["outcome"]["label"], "reason": status["outcome"]["reason"]}

    def nothing_offered():
        package = ctx["status"]["package_id"]
        assert client.post(f"/work-packages/{package}/accept", headers=auth).status_code == 409
        bundle = client.get(f"/work-packages/{package}/bundle.zip", headers=auth)
        names = _zip_names(bundle.content)
        report_md = zipfile.ZipFile(io.BytesIO(bundle.content)).read("report.md").decode()
        assert names == ["report.md"] and report_md.startswith("# FAILED"), (names, report_md[:200])
        rows = ctx["status"]["criteria_evidence"]
        assert rows and rows[0]["status"] == "not_met" and rows[0]["evaluated"], rows
        return {"bundle": names, "criteria": [r["status"] for r in rows]}

    j.step("a failing build ends FAILED with no approval", submit)
    j.step("nothing from it can be accepted or downloaded as work", nothing_offered)
    if "job" in ctx:
        snapshot["failed"] = {"project_id": ctx["job"]["project_id"]}


def _journey_clarification(client, auth, report, snapshot):
    j = Journey("clarification -> answer -> same package continues", report)
    ctx: dict = {}

    def ask():
        ctx["job"] = _submit(client, auth, {"objective": "Work out a total for the office order.\nNEED: Which two numbers should I add?"})
        status = _wait(client, auth, ctx["job"]["project_id"], lambda s: s.get("workflow_state") == "AWAITING_CLARIFICATION" or _settled(s) and s.get("status") != "waiting")
        assert status["workflow_state"] == "AWAITING_CLARIFICATION", status
        assert status["outcome"]["state"] == "NEEDS_CLARIFICATION" and status["question"], status
        ctx["status"] = status
        return {"question": status["question"], "outcome": status["outcome"]["label"]}

    def empty_refused():
        package = ctx["status"]["package_id"]
        response = client.post(f"/work-packages/{package}/clarification", json={"answer": "  "}, headers=auth)
        assert response.status_code == 400, response.text
        return {"refused": response.json()["detail"]}

    def answer():
        package = ctx["status"]["package_id"]
        response = client.post(f"/work-packages/{package}/clarification", json={"answer": "4 + 7"}, headers=auth)
        assert response.status_code == 200, response.text
        status = _wait(client, auth, ctx["job"]["project_id"], lambda s: s.get("workflow_state") not in ("AWAITING_CLARIFICATION", "IN_PROGRESS"))
        assert status["package_id"] == package, status  # the same package continued
        assert status["outcome"]["state"] != "NEEDS_CLARIFICATION", status["outcome"]
        return {"after": status["outcome"]["label"], "workflow_state": status["workflow_state"]}

    for name, fn in (("a missing fact pauses the job with a question", ask), ("an empty answer is refused", empty_refused),
                     ("the answer continues the same package", answer)):
        j.step(name, fn)
    if "job" in ctx:
        snapshot["clarify"] = {"project_id": ctx["job"]["project_id"]}


def _journey_budgets(client, auth, report):
    j = Journey("budgets keep currency and amount, or are refused with a reason", report)

    def count() -> int:
        from app.db import connect

        conn = connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
        finally:
            conn.close()

    def unsupported():
        before = count()
        response = client.post("/milo/jobs", json={"objective": "Write a summary of office chairs.", "job_cap_gbp": 500}, headers=auth)
        assert response.status_code == 400 and "£500.00" in response.json()["detail"], response.text
        assert count() == before, "a refused budget left a job behind"
        return {"refused": response.json()["detail"][:200]}

    def dollars():
        job = _submit(client, auth, {"objective": "Write a small Python program that adds 4 and 7 and prints the sum. Dollar case.", "budget_usd": 1.5})
        from app.goals import get_goal

        goal = get_goal(job["project_id"])
        assert (goal["job_cap_currency"], goal["job_cap_amount"]) == ("USD", 1.5), goal
        _wait(client, auth, job["project_id"], _settled)
        return {"currency": goal["job_cap_currency"], "amount": goal["job_cap_amount"]}

    def monthly():
        view = client.put("/budget", json={"monthly_cap_gbp": 41.17}, headers=auth).json()
        assert view["monthly_cap_gbp"] == 41.17 and view["monthly_cap_currency"] == "GBP", view
        return {"monthly_cap_gbp": view["monthly_cap_gbp"]}

    for name, fn in (("an unsupported budget is refused and creates nothing", unsupported),
                     ("a dollar budget stays in dollars", dollars), ("the monthly cap keeps its pounds", monthly)):
        j.step(name, fn)


def verify_after_restart(snapshot_path: Path, phase: str = "restart") -> dict:
    """A fresh process on the same database must see every job exactly as before."""
    from fastapi.testclient import TestClient

    from app.main import app

    report = {"phase": phase, "steps": []}
    auth = {"Authorization": f"Bearer {AUTH_KEY}"}
    snapshot = json.loads(snapshot_path.read_text())
    title = ("restart: a new process sees the same jobs, outcomes, checks and files" if phase == "restart"
             else "new instance (local workflow store wiped): the same jobs, outcomes, checks and files")
    j = Journey(title, report)
    with TestClient(app) as client:
        for name, before in sorted(snapshot.items()):
            def check(name=name, before=before):
                after = _observe(client, auth, before["project_id"])
                drift = {k: (before.get(k), after.get(k)) for k in after if before.get(k) != after.get(k)}
                assert not drift, f"{name} changed across the restart: {drift}"
                return {"outcome": after["outcome_label"], "deliverable_sha": after["deliverable_sha"]}
            j.step(f"{name} job unchanged", check)
        def budget():
            view = client.get("/budget", headers=auth).json()
            assert view["monthly_cap_gbp"] == 41.17, view
            return {"monthly_cap_gbp": view["monthly_cap_gbp"]}
        j.step("monthly budget unchanged", budget)
    return report


# --- hosted (separate evidence) -------------------------------------------------------------------------

def run_hosted(base_url: str, job_id: str = "", allow_writes: bool = False) -> dict:
    import httpx

    key = os.environ.get("AYVEN_ACCEPTANCE_KEY", "")
    if not key:
        # C12: hosted evidence without the key would only check public endpoints yet still "pass".
        raise SystemExit("AYVEN_ACCEPTANCE_KEY is required for --hosted; refusing to report a hosted result without it.")
    headers = {"Authorization": f"Bearer {key}"}
    report = {"phase": "hosted", "base_url": base_url, "writes_allowed": allow_writes, "steps": []}
    client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=30.0)
    j = Journey("hosted service (read-only unless --allow-writes)", report)

    def health():
        body = client.get("/health").json()
        return {k: body.get(k) for k in ("version", "label", "simulated", "runtime", "durable", "persistence",
                                         "persistence_state", "workflow_store", "workflow_store_durable", "access_protected")}

    def protected():
        response = httpx.get(base_url.rstrip("/") + "/state", timeout=30.0)
        assert response.status_code in (401, 403, 307, 302), f"unauthenticated /state returned {response.status_code}"
        return {"unauthenticated_state": response.status_code}

    j.step("health is reachable and says what it is", health)
    j.step("the service refuses unauthenticated reads", protected)
    if job_id:
        def one_job():
            status = client.get(f"/milo/jobs/{job_id}").json()
            outcome = status.get("outcome") or {}
            assert outcome, "this deployment does not report an outcome yet (older build?)"
            return {"workflow_state": status.get("workflow_state"), "outcome": outcome.get("label"),
                    "criteria": status.get("criteria_results")}
        j.step(f"job {job_id} reports its outcome", one_job)
    if allow_writes:
        def refused_budget():
            response = client.post("/milo/jobs", json={"objective": "Acceptance harness budget probe.", "job_cap_gbp": 100000})
            assert response.status_code == 400, response.text
            return {"refused": response.json().get("detail")}
        j.step("an unsupported budget is refused (creates nothing)", refused_budget)
    return report


# --- entry point ----------------------------------------------------------------------------------------

def _child(phase: str, snapshot: str, out: str) -> int:
    path = Path(snapshot)
    report = run_simulated_journeys(path) if phase == "journeys" else verify_after_restart(path, phase)
    Path(out).write_text(json.dumps(report, indent=2, default=str))
    return 0


def _git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def run_simulated(timeout: int = 900) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ayven-acceptance-"))
    env = simulated_env(work / "campus.sqlite")
    snapshot = work / "snapshot.json"
    combined = {
        "evidence": "SIMULATED",
        "description": "In-process Campus, deterministic role generator, temporary local SQLite. Not hosted evidence.",
        "commit": _git_head(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
    }
    for phase in ("journeys", "restart", "new-instance"):
        out = work / f"{phase}.json"
        if phase == "new-instance":
            # A new hosted instance keeps the app database (Turso) but not this disk: the
            # workflow engine's local tables are gone. Jobs must still read back unchanged.
            for leftover in work.glob("ayven-workflows.sqlite*"):
                leftover.unlink()
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", phase, "--snapshot", str(snapshot), "--out", str(out)],
                              cwd=str(API), env=env, capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            combined["steps"].append({"journey": phase, "step": "process ran", "passed": False,
                                      "evidence": {"exit": proc.returncode, "log": (proc.stdout + proc.stderr)[-4000:]}})
            break
        combined["steps"].extend(json.loads(out.read_text())["steps"])
    combined["finished_at"] = datetime.now(timezone.utc).isoformat()
    return combined


def summarise(report: dict) -> tuple[int, int, int]:
    passed = sum(1 for s in report["steps"] if s.get("passed") is True)
    failed = sum(1 for s in report["steps"] if s.get("passed") is False)
    skipped = sum(1 for s in report["steps"] if s.get("skipped"))
    return passed, failed, skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hosted", default="", help="base URL of a deployed Campus (separate HOSTED evidence)")
    parser.add_argument("--job", default="", help="hosted only: a project id to read")
    parser.add_argument("--allow-writes", action="store_true", help="hosted only: allow requests that could create rows")
    parser.add_argument("--report", default="", help="write the JSON report here")
    parser.add_argument("--child", default="", help=argparse.SUPPRESS)
    parser.add_argument("--snapshot", default="", help=argparse.SUPPRESS)
    parser.add_argument("--out", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return _child(args.child, args.snapshot, args.out)
    if args.hosted:
        report = run_hosted(args.hosted, args.job, args.allow_writes)
        report.update(evidence="HOSTED", commit_of_harness=_git_head(), checked_at=datetime.now(timezone.utc).isoformat())
    else:
        report = run_simulated()
    passed, failed, skipped = summarise(report)
    report["summary"] = {"passed": passed, "failed": failed, "skipped": skipped}
    text = json.dumps(report, indent=2, default=str)
    if args.report:
        Path(args.report).write_text(text)
    for step in report["steps"]:
        mark = "PASS" if step.get("passed") is True else ("SKIP" if step.get("skipped") else "FAIL")
        print(f"[{report['evidence']}] {mark}  {step['journey']} :: {step['step']}")
    print(f"[{report['evidence']}] {passed} passed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 and skipped == 0 and passed > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
