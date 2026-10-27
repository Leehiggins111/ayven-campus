"""Workbench orders are mirrored onto the Campus records Lee monitors (project row, agent, events).

Before this, a Workbench order left its project 'running' forever and no agent or event
changed, so a later job with the same objective was merged into the dead project and never ran.
"""
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.app.main import app
from apps.api.app.db import connect, init_db, reset_connection_state
from tests.test_workbench_bridge_http import evidence_for

OWNER = "o" * 40
WORKER = "w" * 40
OBJECTIVE = "Add a function add(a,b) to calc.py returning a+b, with a unit test."


class CampusSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "AYVEN_DB": str(Path(self.tmp.name) / "campus.sqlite"), "AYVEN_ENV": "local",
            "AYVEN_API_TOKEN": OWNER, "AYVEN_WORKER_TOKEN": WORKER,
            "AYVEN_LLM_STUB": "1", "AYVEN_THREAD_STUB": "1"})
        self.env.start()
        reset_connection_state()
        c = connect(); init_db(c); c.close()
        self.client = TestClient(app)
        self.owner = {"Authorization": "Bearer " + OWNER}
        self.worker = {"Authorization": "Bearer " + WORKER}

    def tearDown(self):
        self.env.stop()
        reset_connection_state()
        self.tmp.cleanup()

    def enqueue(self, key="order-1"):
        r = self.client.post("/milo/executions", headers={**self.owner, "Idempotency-Key": key},
                             json={"objective": OBJECTIVE, "project_alias": "cloud-demo",
                                   "acceptance_criteria": ["tests pass"], "job_cap_gbp": 0,
                                   "allowed_files": ["calc.py", "test_calc.py"]})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["project_id"]

    def claim(self, worker="wb-1"):
        return self.client.post("/internal/workbench/claim", headers=self.worker, json={"worker_id": worker}).json()["job"]

    def result(self, job, **body):
        payload = {"worker_id": "wb-1", "fence": job["fence"], "evidence": [], "defects": [], "artifacts": []}
        payload.update(body)
        r = self.client.post("/internal/workbench/%s/result" % job["order_id"], headers=self.worker, json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    @staticmethod
    def change():
        diff = ("diff --git a/calc.py b/calc.py\nnew file mode 100644\n--- /dev/null\n+++ b/calc.py\n"
                "@@ -0,0 +1 @@\n+x\n")
        return {"name": "changes.diff", "content": diff, "sha256": hashlib.sha256(diff.encode()).hexdigest()}

    @staticmethod
    def campus(project_id):
        c = connect()
        try:
            project = c.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()["status"]
            agent = dict(c.execute("SELECT status,visual_state,current_task_id FROM agents WHERE id='workbench'").fetchone() or {})
            kinds = [r["type"] for r in c.execute(
                "SELECT type FROM events WHERE project_id=? ORDER BY timestamp", (project_id,)).fetchall()]
        finally:
            c.close()
        return project, agent, kinds

    def test_queued_and_claimed_orders_are_visible_on_campus(self):
        pid = self.enqueue()
        project, agent, kinds = self.campus(pid)
        self.assertEqual(project, "running")
        self.assertEqual((agent["status"], agent["current_task_id"]), ("waiting", "order-1"))
        self.assertIn("workbench.queued", kinds)
        self.assertEqual(self.claim()["order_id"], "order-1")
        project, agent, kinds = self.campus(pid)
        self.assertEqual(agent["status"], "working")
        self.assertIn("workbench.running", kinds)
        # The worker is listed with the rest of the workforce in the Campus state view.
        state = self.client.get("/state", headers=self.owner).json()
        self.assertIn("workbench", [a["id"] for a in state["agents"]])

    def test_failed_order_closes_the_project_so_the_same_job_can_run_again(self):
        pid = self.enqueue()
        job = self.claim()
        self.assertEqual(self.result(job, verdict="BLOCKED", execution_mode="NONE",
                                     defects=["Workbench panel unavailable"])["state"], "failed")
        project, agent, kinds = self.campus(pid)
        self.assertEqual(project, "rejected")
        self.assertEqual(agent["status"], "error")
        self.assertIn("workbench.failed", kinds)
        again = self.client.post("/milo/jobs", headers=self.owner, json={"objective": OBJECTIVE})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertFalse(again.json()["duplicate"])
        self.assertNotEqual(again.json()["project_id"], pid)

    def test_simulated_result_is_recorded_not_left_running(self):
        pid = self.enqueue()
        job = self.claim()
        change = self.change()
        self.assertEqual(self.result(job, verdict="PASS", execution_mode="SIMULATED", evidence=["fixture"],
                                     artifacts=[change, *evidence_for(change)])["state"], "simulated")
        project, agent, kinds = self.campus(pid)
        self.assertEqual(project, "unresolved")
        self.assertIn("workbench.simulated", kinds)

    def test_accepted_result_completes_the_project(self):
        pid = self.enqueue()
        job = self.claim()
        change = self.change()
        self.assertEqual(self.result(job, verdict="PASS", execution_mode="REAL", evidence=["2 tests passed"],
                                     artifacts=[change, *evidence_for(change)])["state"], "awaiting_approval")
        project, agent, _ = self.campus(pid)
        self.assertEqual((project, agent["status"]), ("waiting", "needs_approval"))
        r = self.client.post("/milo/executions/order-1/decision", headers=self.owner, json={"decision": "accept"})
        self.assertEqual(r.status_code, 200, r.text)
        project, agent, kinds = self.campus(pid)
        self.assertEqual((project, agent["status"], agent["visual_state"]), ("complete", "idle", "COMPLETED"))
        self.assertIn("workbench.completed", kinds)

    def test_rejected_result_closes_the_project(self):
        pid = self.enqueue()
        job = self.claim()
        change = self.change()
        self.result(job, verdict="PASS", execution_mode="REAL", evidence=["ok"], artifacts=[change, *evidence_for(change)])
        r = self.client.post("/milo/executions/order-1/decision", headers=self.owner,
                             json={"decision": "reject", "reason": "Not what I asked for"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.campus(pid)[0], "rejected")

    def test_lease_expired_after_final_attempt_fails_the_project(self):
        pid = self.enqueue()
        self.claim()
        c = connect()
        c.execute("UPDATE workbench_orders SET attempts=3, lease_until=? WHERE id='order-1'", (int(time.time()) - 5,))
        c.commit(); c.close()
        self.assertIsNone(self.claim("wb-2"))
        project, agent, kinds = self.campus(pid)
        self.assertEqual((project, agent["status"]), ("rejected", "error"))
        self.assertIn("workbench.failed", kinds)


if __name__ == "__main__":
    unittest.main()
