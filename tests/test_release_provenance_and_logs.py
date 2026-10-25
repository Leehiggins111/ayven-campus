"""Release hardening (24 Oct 2026, Claude Code).

D12: REAL / SIMULATED is derived by Campus from the Workbench-signed model provenance in qa-run.json, never from the
worker's own label. A result from an unverified model (a stub endpoint) can never wait for or receive approval.
Failure logs: the worker's log artifacts and logs-manifest.json are stored and previewable for a failed order.
"""
import os
from unittest import mock

from apps.api.app.access import validate_public_configuration
from test_workbench_evidence_http import TestEvidence
from test_workbench_scope_http import patch_for
from workbench_signed import artifact, signed_evidence

STUB = {"kind": "unverified", "endpoint": "http://127.0.0.1:9999", "model": "qwen3:4b-instruct",
        "ollama_version": None, "digest": None, "calls": 3, "served_models": ["None"], "fingerprints": ["None"],
        "problems": ["no Ollama /api/version at http://127.0.0.1:9999: HTTPError"]}


class Provenance(TestEvidence):
    def with_provenance(self, prov):
        def build(order_id):
            p = patch_for("calc.py")
            return [p] + signed_evidence(p, order_id, ["calc.py"], mutate=lambda r: r.update(model_provenance=prov))
        return build

    def test_a_verified_ollama_model_is_real_and_reaches_review(self):
        job, reply, result = self.submit("ollama", self.good)
        self.assertEqual(reply["state"], "awaiting_approval", result)
        self.assertEqual(result["execution_mode"], "REAL")
        self.assertEqual(result["model_provenance"]["kind"], "ollama")
        self.assertEqual(result["model_provenance"]["digest"], "sha256:" + "d" * 64)

    def test_a_stub_endpoint_is_simulated_even_when_the_worker_says_real(self):
        job, reply, result = self.submit("stub", self.with_provenance(STUB), mode="REAL")
        self.assertEqual(reply["state"], "simulated", result)
        self.assertEqual(result["execution_mode"], "SIMULATED")
        self.assertEqual(result["worker_execution_mode"], "REAL")
        self.assertIn("no Ollama", result["model_provenance"]["problems"][0])
        sha = next(a["sha256"] for a in result["artifacts"] if a["name"] == "changes.diff")
        r = self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner,
                             json={"decision": "accept", "patch_sha256": sha})
        self.assertEqual(r.status_code, 409)

    def test_a_record_without_provenance_is_simulated(self):
        job, reply, result = self.submit("noprov", self.with_provenance(None), mode="REAL")
        self.assertEqual((reply["state"], result["execution_mode"]), ("simulated", "SIMULATED"))

    def test_problems_in_the_provenance_make_it_simulated_whatever_the_kind(self):
        bad = {"kind": "ollama", "problems": ["responses named a different model or none: None"]}
        job, reply, result = self.submit("mixed", self.with_provenance(bad), mode="REAL")
        self.assertEqual(reply["state"], "simulated")

    def test_a_real_claim_without_a_signed_record_is_not_kept(self):
        self.assertEqual(self.enqueue("fail-real", ["calc.py"]).status_code, 200)
        job = self.claim()
        r = self.client.post("/internal/workbench/%s/result" % job["order_id"], headers=self.worker,
                             json={"worker_id": "w1", "fence": job["fence"], "verdict": "FAIL", "execution_mode": "REAL",
                                   "evidence": [], "defects": ["tests fail"], "artifacts": []})
        self.assertEqual(r.json()["state"], "failed")
        result = self.status(job)["result"]
        self.assertEqual((result["execution_mode"], result["worker_execution_mode"]), ("NONE", "REAL"))

    def test_the_drill_override_needs_the_test_environment(self):
        with mock.patch.dict(os.environ, {"AYVEN_TEST_ACCEPT_UNVERIFIED_MODEL": "1", "AYVEN_ENV": "local"}):
            with self.assertRaisesRegex(RuntimeError, "AYVEN_ENV=test"):
                validate_public_configuration()
            job, reply, result = self.submit("override-local", self.with_provenance(STUB), mode="REAL")
            self.assertEqual(reply["state"], "simulated")         # not honoured outside the test environment
        with mock.patch.dict(os.environ, {"AYVEN_TEST_ACCEPT_UNVERIFIED_MODEL": "1", "AYVEN_ENV": "test"}):
            job, reply, result = self.submit("override-test", self.with_provenance(STUB), mode="SIMULATED")
            self.assertEqual(reply["state"], "awaiting_approval")
            self.assertIn("not a real model", result["test_override"])
            self.assertEqual(result["model_provenance"]["kind"], "unverified")   # the truth stays on the record


class FailureLogs(TestEvidence):
    def test_failure_logs_are_kept_and_previewable_but_other_names_are_not(self):
        self.assertEqual(self.enqueue("logs", ["calc.py"]).status_code, 200)
        job = self.claim()
        dev_log = "--- turn 1\n{\"action\":\"run\",\"cmd\":\"python3 -m unittest\"}\nexit 1\nTraceback ...\n"
        manifest = '{"goal": "g", "logs": [{"name": "log-dev-abc123-a1.log", "complete": true}]}'
        arts = [artifact("log-dev-abc123-a1.log", dev_log), artifact("logs-manifest.json", manifest)]
        r = self.client.post("/internal/workbench/%s/result" % job["order_id"], headers=self.worker,
                             json={"worker_id": "w1", "fence": job["fence"], "verdict": "BLOCKED",
                                   "execution_mode": "NONE", "evidence": [],
                                   "defects": ["dev task failed: NO PROGRESS: Turn limit reached without finish."],
                                   "artifacts": arts})
        self.assertEqual(r.json()["state"], "failed")
        for name, body in (("log-dev-abc123-a1.log", dev_log), ("logs-manifest.json", manifest)):
            got = self.client.get("/milo/executions/%s/preview/%s" % (job["order_id"], name), headers=self.owner)
            self.assertEqual(got.status_code, 200, name)
            self.assertEqual(got.text, body)
        for name in ("secrets.env", "log-../x", "logs-other.json"):
            got = self.client.get("/milo/executions/%s/preview/%s" % (job["order_id"], name), headers=self.owner)
            self.assertEqual(got.status_code, 404, name)
