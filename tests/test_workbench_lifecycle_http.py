"""Order lifecycle against the real Campus app: idempotency under concurrency, leases and fencing, cancellation,
time and token limits, clarification and rework revisions, durable events."""
import threading
import time

from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.db import connect
from apps.api.app.workbench_evidence import request_key
from test_workbench_scope_http import Orders, patch_for
from workbench_signed import signed_evidence


class Lifecycle(Orders):
    def order(self, key="life", **extra):
        body = {"objective": "Add multiply", "project_alias": "cloud-demo", "acceptance_criteria": ["tests pass"],
                "job_cap_gbp": 0, "allowed_files": ["calc.py"], **extra}
        return self.client.post("/milo/executions", headers={**self.owner, "Idempotency-Key": key}, json=body)

    def beat(self, job, worker="w1", **progress):
        return self.client.post("/internal/workbench/%s/heartbeat" % job["order_id"], headers=self.worker,
                                json={"worker_id": worker, "fence": job["fence"], "progress": progress or None})

    def post(self, job, verdict="PASS", mode="REAL", artifacts=None, worker="w1", **extra):
        return self.client.post("/internal/workbench/%s/result" % job["order_id"], headers=self.worker,
                                json={"worker_id": worker, "fence": job["fence"], "verdict": verdict,
                                      "execution_mode": mode, "evidence": ["verified"] if verdict == "PASS" else [],
                                      "defects": [] if verdict in ("PASS", "CLARIFY") else ["tests fail"],
                                      "artifacts": artifacts or [], **extra})

    def signed(self, job, revision=0):
        p = patch_for("calc.py")
        return [p] + signed_evidence(p, job["order_id"], ["calc.py"], revision=revision)

    def events(self, order_id):
        return [e["kind"] for e in self.client.get("/milo/executions/%s/events" % order_id,
                                                    headers=self.owner).json()["events"]]

    def age(self, order_id, **cols):
        c = connect()
        c.execute("UPDATE workbench_orders SET " + ",".join(k + "=?" for k in cols) + " WHERE id=?",
                  (*cols.values(), order_id))
        c.commit()
        c.close()

    # ---------------------------------------------------------------- idempotency
    def test_concurrent_duplicate_submissions_create_exactly_one_order(self):
        replies, errors = [], []

        def submit():
            try:
                client = TestClient(app)     # no lifespan: startup recovery threads are not under test
                if True:
                    replies.append(client.post("/milo/executions", headers={**self.owner, "Idempotency-Key": "race"},
                                               json={"objective": "Add multiply", "project_alias": "cloud-demo",
                                                     "acceptance_criteria": ["tests pass"], "job_cap_gbp": 0,
                                                     "allowed_files": ["calc.py"]}))
            except Exception as exc:     # surfaced below
                errors.append(exc)
        threads = [threading.Thread(target=submit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors, errors)
        self.assertEqual([r.status_code for r in replies], [200] * 8, [r.text for r in replies])
        self.assertEqual(sum(1 for r in replies if not r.json()["duplicate"]), 1)
        self.assertEqual(len({r.json()["project_id"] for r in replies}), 1)
        c = connect()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM workbench_orders").fetchone()[0], 1)
        c.close()
        self.assertIsNotNone(self.claim())
        self.assertIsNone(self.claim())

    def test_lost_reply_replay_returns_the_same_order(self):
        first = self.order("lost").json()
        again = self.order("lost").json()
        self.assertEqual((again["order_id"], again["project_id"], again["duplicate"]),
                         (first["order_id"], first["project_id"], True))
        self.assertEqual(self.order("lost", max_minutes=5).status_code, 409)   # same key, different order

    # ---------------------------------------------------------------- leases and fencing
    def test_lease_expiry_requeues_with_a_new_fence_and_refuses_the_stale_worker(self):
        self.order()
        old = self.claim()
        self.age(old["order_id"], lease_until=0)
        new = self.client.post("/internal/workbench/claim", headers=self.worker, json={"worker_id": "w2"}).json()["job"]
        self.assertEqual(new["order_id"], old["order_id"])
        self.assertEqual(new["fence"], old["fence"] + 1)
        self.assertEqual(new["request_key"], old["request_key"])     # same Workbench goal: no duplicate execution
        self.assertEqual(self.beat(old).status_code, 409)
        self.assertEqual(self.post(old, artifacts=self.signed(old)).status_code, 409)
        self.assertEqual(self.post(new, worker="w2", artifacts=self.signed(new)).json()["state"], "awaiting_approval")
        self.assertIn("lease_expired", self.events(old["order_id"]))

    def test_lease_expiry_after_max_attempts_fails_honestly(self):
        self.order()
        for _ in range(3):
            job = self.claim()
            self.age(job["order_id"], lease_until=0)
        self.assertIsNone(self.claim())
        status = self.status(job)
        self.assertEqual(status["state"], "failed")
        self.assertIn("lease expired", " ".join(status["result"]["defects"]))

    # ---------------------------------------------------------------- cancellation
    def test_cancel_queued_order_is_terminal_and_never_claimed(self):
        self.order()
        r = self.client.post("/milo/executions/life/cancel", headers=self.owner, json={"reason": "not needed"})
        self.assertEqual(r.json(), {"state": "cancelled", "duplicate": False})
        self.assertTrue(self.client.post("/milo/executions/life/cancel", headers=self.owner, json={}).json()["duplicate"])
        self.assertIsNone(self.claim())

    def test_cancel_running_order_stops_the_worker_and_refuses_its_result(self):
        self.order()
        job = self.claim()
        self.assertEqual(self.beat(job, stage="dev turn 1").status_code, 200)
        self.client.post("/milo/executions/life/cancel", headers=self.owner, json={})
        hb = self.beat(job)
        self.assertEqual(hb.status_code, 409)
        self.assertIn("cancelled", hb.text)
        self.assertEqual(self.post(job, artifacts=self.signed(job)).status_code, 409)
        self.assertEqual(self.status(job)["state"], "cancelled")
        self.assertEqual(self.client.post("/milo/executions/life/decision", headers=self.owner,
                                          json={"decision": "accept"}).status_code, 409)

    def test_cancel_is_refused_once_a_result_exists(self):
        self.order()
        job = self.claim()
        self.post(job, artifacts=self.signed(job))
        self.assertEqual(self.client.post("/milo/executions/life/cancel", headers=self.owner, json={}).status_code, 409)

    # ---------------------------------------------------------------- limits
    def test_time_limit_fails_the_order_and_stops_the_worker(self):
        self.order(max_minutes=1)
        job = self.claim()
        self.age(job["order_id"], started_at=int(time.time()) - 120)
        self.assertEqual(self.beat(job).status_code, 409)
        status = self.status(job)
        self.assertEqual(status["state"], "failed")
        self.assertIn("Time limit", status["result"]["defects"][0])

    def test_token_budget_exhaustion_fails_the_order(self):
        self.order(token_cap=1000)
        job = self.claim()
        self.assertEqual(self.beat(job, stage="dev turn 2", tokens=400).status_code, 200)
        r = self.beat(job, stage="dev turn 5", tokens=1200)
        self.assertEqual(r.status_code, 409)
        self.assertIn("Budget exhausted", r.text)
        status = self.status(job)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["usage"]["tokens"], 1200)
        self.assertEqual(self.post(job, artifacts=self.signed(job)).status_code, 409)

    def test_claim_carries_limits_and_request_key(self):
        self.order(token_cap=5000, max_minutes=7, allow_clarification=True)
        job = self.claim()
        self.assertEqual((job["token_cap"], job["max_minutes"], job["allow_clarification"], job["revision"]),
                         (5000, 7, True, 0))
        self.assertEqual(job["request_key"], request_key("life", 0))
        self.assertGreater(job["deadline"], time.time())

    # ---------------------------------------------------------------- clarification and rework
    def test_clarification_pause_and_continuation(self):
        self.order(allow_clarification=True)
        job = self.claim()
        r = self.post(job, verdict="CLARIFY", mode="NONE", question="Should delivery be free at exactly 25 pounds?")
        self.assertEqual(r.json()["state"], "needs_clarification", r.text)
        status = self.status(job)
        self.assertEqual(status["question"], "Should delivery be free at exactly 25 pounds?")
        milo = self.client.get("/milo/jobs/" + status["project_id"], headers=self.owner).json()
        self.assertTrue(milo["waiting_for_lee"])
        self.assertIn("25 pounds", milo["question"])
        self.assertEqual(self.client.post("/milo/executions/life/decision", headers=self.owner,
                                          json={"decision": "accept"}).status_code, 409)
        h = {**self.owner, "Idempotency-Key": "answer-1"}
        a = self.client.post("/milo/executions/life/revise", headers=h, json={"kind": "clarification", "text": "Yes."})
        self.assertEqual(a.json(), {"state": "queued", "revision": 1, "duplicate": False})
        self.assertTrue(self.client.post("/milo/executions/life/revise", headers=h,
                                         json={"kind": "clarification", "text": "Yes."}).json()["duplicate"])
        job2 = self.claim()
        self.assertEqual(job2["revision"], 1)
        self.assertNotEqual(job2["request_key"], job["request_key"])     # a new Workbench goal for the new brief
        self.assertEqual(job2["revisions"][0]["question"], "Should delivery be free at exactly 25 pounds?")
        self.assertEqual(job2["revisions"][0]["text"], "Yes.")
        stale = self.post(job2, artifacts=self.signed(job2, revision=0))   # evidence from revision 0 is refused
        self.assertEqual(stale.json()["state"], "failed")
        self.assertIn("revised", self.events("life"))

    def test_questions_are_refused_when_the_order_does_not_allow_them(self):
        self.order()
        job = self.claim()
        r = self.post(job, verdict="CLARIFY", mode="NONE", question="Which file?")
        self.assertEqual(r.json()["state"], "failed")

    def test_rework_after_failure_starts_a_new_revision_and_archives_the_old_result(self):
        self.order()
        job = self.claim()
        self.assertEqual(self.post(job, verdict="FAIL").json()["state"], "failed")
        self.assertEqual(self.client.post("/milo/executions/life/revise", headers=self.owner,
                                          json={"kind": "clarification", "text": "x"}).status_code, 409)
        r = self.client.post("/milo/executions/life/revise", headers=self.owner,
                             json={"kind": "rework", "text": "Handle the threshold exactly."})
        self.assertEqual(r.json()["revision"], 1)
        job2 = self.claim()
        self.assertEqual(self.post(job2, artifacts=self.signed(job2, revision=1)).json()["state"], "awaiting_approval")
        c = connect()
        archived = c.execute("SELECT state FROM workbench_history WHERE order_id='life' AND revision=0").fetchone()
        c.close()
        self.assertEqual(archived["state"], "failed")

    def test_rework_is_refused_for_running_or_approvable_work(self):
        self.order()
        job = self.claim()
        self.assertEqual(self.client.post("/milo/executions/life/revise", headers=self.owner,
                                          json={"kind": "rework", "text": "x"}).status_code, 409)
        self.post(job, artifacts=self.signed(job))
        self.assertEqual(self.client.post("/milo/executions/life/revise", headers=self.owner,
                                          json={"kind": "rework", "text": "x"}).status_code, 409)

    # ---------------------------------------------------------------- events and authority
    def test_progress_events_are_durable_and_deduplicated(self):
        self.order()
        job = self.claim()
        for stage in ("dev turn 1", "dev turn 1", "qa: trusted verification"):
            self.beat(job, stage=stage)
        self.post(job, artifacts=self.signed(job))
        events = self.client.get("/milo/executions/life/events", headers=self.owner).json()["events"]
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds, ["created", "claimed", "progress", "progress", "result"])
        later = self.client.get("/milo/executions/life/events?after=%d" % events[2]["seq"], headers=self.owner).json()
        self.assertEqual([e["kind"] for e in later["events"]], ["progress", "result"])

    def test_worker_token_cannot_reach_owner_routes_and_owner_cannot_act_as_worker(self):
        self.order()
        for method, path, body in (("post", "/milo/executions/life/decision", {"decision": "accept"}),
                                   ("post", "/milo/executions/life/cancel", {}),
                                   ("post", "/milo/executions/life/revise", {"kind": "rework", "text": "x"}),
                                   ("get", "/milo/executions/life/events", None),
                                   ("get", "/milo/executions", None)):
            r = getattr(self.client, method)(path, headers=self.worker, **({"json": body} if body is not None else {}))
            self.assertIn(r.status_code, (401, 403), (path, r.text))
        self.assertEqual(self.client.post("/internal/workbench/claim", headers=self.owner,
                                          json={"worker_id": "x"}).status_code, 401)
