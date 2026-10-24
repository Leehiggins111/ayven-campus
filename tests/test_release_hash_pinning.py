"""Release 20261024: only the exact reviewed result can be accepted and released (hash pinning), and the order
view carries genuine progress taken from worker heartbeats (G7)."""
import hashlib

from apps.api.app.db import connect
from test_workbench_evidence_http import TestEvidence


class HashPinning(TestEvidence):
    def accepted_job(self, key):
        job, reply, result = self.submit(key, self.good)
        self.assertEqual(reply["state"], "awaiting_approval", result)
        sha = next(a["sha256"] for a in result["artifacts"] if a["name"] == "changes.diff")
        return job, sha

    def decide(self, job, **body):
        return self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner, json=body)

    def test_accept_without_reviewed_digest_is_refused(self):
        job, _ = self.accepted_job("nodigest")
        self.assertEqual(self.decide(job, decision="accept").status_code, 400)
        self.assertEqual(self.status(job)["state"], "awaiting_approval")

    def test_accept_of_a_different_diff_is_refused(self):
        job, sha = self.accepted_job("otherdiff")
        other = hashlib.sha256(b"a diff the owner never saw").hexdigest()
        self.assertEqual(self.decide(job, decision="accept", patch_sha256=other).status_code, 409)
        self.assertEqual(self.decide(job, decision="accept", patch_sha256=sha, revision=7).status_code, 409)
        self.assertEqual(self.status(job)["state"], "awaiting_approval")

    def test_accepted_digest_is_recorded_and_download_is_pinned(self):
        job, sha = self.accepted_job("pinned")
        self.assertEqual(self.decide(job, decision="accept", patch_sha256=sha, revision=0).status_code, 200)
        view = self.status(job)
        self.assertEqual(view["accepted_patch_sha256"], sha)
        url = "/milo/executions/%s/artifacts/changes.diff" % job["order_id"]
        got = self.client.get(url, headers=self.owner)
        self.assertEqual(got.status_code, 200)
        self.assertEqual(hashlib.sha256(got.content).hexdigest(), sha)
        # Someone alters the stored diff after acceptance: it is never released.
        c = connect()
        c.execute("UPDATE workbench_artifacts SET content=? WHERE order_id=? AND name='changes.diff'",
                  (b"diff --git a/evil.py b/evil.py\n", job["order_id"]))
        c.commit(); c.close()
        self.assertEqual(self.client.get(url, headers=self.owner).status_code, 409)

    def test_reject_needs_no_digest_but_never_releases(self):
        job, _ = self.accepted_job("reject")
        self.assertEqual(self.decide(job, decision="reject", reason="Not what I asked for").status_code, 200)
        view = self.status(job)
        self.assertEqual(view["state"], "rejected")
        self.assertIsNone(view["accepted_patch_sha256"])
        self.assertEqual(self.client.get("/milo/executions/%s/artifacts/changes.diff" % job["order_id"],
                                         headers=self.owner).status_code, 409)

    def test_progress_comes_from_the_heartbeat(self):
        self.assertEqual(self.enqueue("progress", ["calc.py"]).status_code, 200)
        job = self.claim()
        hb = self.client.post("/internal/workbench/%s/heartbeat" % job["order_id"], headers=self.worker,
                              json={"worker_id": job.get("worker_id", "w1"), "fence": job["fence"],
                                    "progress": {"stage": "dev: turn 3", "tokens": 1234, "cost_gbp": 0}})
        self.assertEqual(hb.status_code, 200, hb.text)
        p = self.status(job)["progress"]
        self.assertEqual(p["stage"], "dev: turn 3")
        self.assertEqual(p["tokens"], 1234)
        self.assertEqual(p["state"], "running")
        self.assertIsNotNone(p["last_heartbeat_at"])
        self.assertLessEqual(p["heartbeat_age_s"], 5)
        self.assertGreater(p["lease_remaining_s"], 0)
        listed = self.client.get("/milo/executions", headers=self.owner).json()["executions"]
        self.assertEqual(next(x for x in listed if x["order_id"] == job["order_id"])["progress"]["stage"],
                         "dev: turn 3")
