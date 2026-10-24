"""C3 and C12 regression for the Campus <-> Workbench bridge (real FastAPI app, temporary database).

C3: orders carry an exact allow-list; Campus re-checks the returned patch itself and never trusts the
worker's PASS for scope. Fixtures reconstruct the two calculator executions' shapes:
  * Execution B (order live-qwen-c2e70b12bae7): allowed calculator.py, patch also changes
    test_calculator.py -> Campus downgrades the PASS, acceptance and download are refused.
  * Execution A: allowed calc.py + test_calc.py, patch changes both -> awaiting approval, acceptable.
C12: a missing access key fails closed outside explicit local mode; a result where nothing ran says
execution_mode NONE (never REAL); MCP actions are presented with their real approval state.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from apps.api.app.main import app
from apps.api.app.db import connect, init_db, reset_connection_state

OWNER = "o" * 40
WORKER = "w" * 40


def artifact(name, content):
    return {"name": name, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}


def evidence_for(patch, order_id, allowed):
    """The Workbench's signed verification of this exact patch for this order (see workbench_signed)."""
    return signed_evidence(patch, order_id, allowed)


def patch_for(*paths, body="+x\n"):
    return artifact("changes.diff", "".join(
        "diff --git a/%s b/%s\nindex 1111111..2222222 100644\n--- a/%s\n+++ b/%s\n@@ -1 +1,2 @@\n%s" % (p, p, p, p, body)
        for p in paths))


from workbench_signed import KEY as ATTESTATION_KEY, signed_evidence  # noqa: E402


class Base(unittest.TestCase):
    env_extra = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"AYVEN_DB": str(Path(self.tmp.name) / "campus.sqlite"), "AYVEN_ENV": "local",
               "AYVEN_API_TOKEN": OWNER, "AYVEN_WORKER_TOKEN": WORKER, "AYVEN_LLM_STUB": "1",
               "AYVEN_ATTESTATION_KEY": ATTESTATION_KEY}
        env.update({k: v for k, v in self.env_extra.items() if v is not None})
        for k, v in self.env_extra.items():
            if v is None:
                env.pop(k, None)
        self.env = patch.dict(os.environ, env)   # restores removed keys on stop()
        self.env.start()
        for key, value in self.env_extra.items():
            if value is None:
                os.environ.pop(key, None)
        reset_connection_state()
        c = connect()
        init_db(c)
        c.close()
        self.client = TestClient(app)
        self.owner = {"Authorization": "Bearer " + OWNER}
        self.worker = {"Authorization": "Bearer " + WORKER}

    def tearDown(self):
        self.env.stop()
        reset_connection_state()
        self.tmp.cleanup()


class Orders(Base):
    """Shared helpers; no tests here."""

    def enqueue(self, key, allowed):
        body = {"objective": "Add multiply", "project_alias": "cloud-demo",
                "acceptance_criteria": ["tests pass"], "job_cap_gbp": 0}
        if allowed is not ...:
            body["allowed_files"] = allowed
        return self.client.post("/milo/executions", headers={**self.owner, "Idempotency-Key": key}, json=body)

    def claim(self):
        return self.client.post("/internal/workbench/claim", headers=self.worker, json={"worker_id": "w1"}).json()["job"]

    def report(self, job, artifacts, verdict="PASS", mode="REAL", evidence=True):
        patches = [a for a in artifacts if a["name"] == "changes.diff"]
        if verdict == "PASS" and evidence and patches:      # a genuine passing test run of this patch
            allowed = self.status(job).get("allowed_files") or []
            artifacts = artifacts + evidence_for(patches[0], job["order_id"], allowed)
        return self.client.post("/internal/workbench/%s/result" % job["order_id"], headers=self.worker,
                                json={"worker_id": "w1", "fence": job["fence"], "verdict": verdict,
                                      "execution_mode": mode, "evidence": ["2 tests passed"],
                                      "defects": [], "artifacts": artifacts})

    def status(self, job):
        return self.client.get("/milo/executions/" + job["order_id"], headers=self.owner).json()


class AllowList(Orders):
    def test_orders_without_a_valid_allow_list_are_refused_and_not_created(self):
        for i, bad in enumerate((..., None, [], ["../x.py"], ["src/*.py"], [".git/config"], ["src/"],
                                 [".github/workflows/ci.yml"], ["a.py", "a.py"], ["C:/x.py"])):
            r = self.enqueue("bad-%d" % i, bad)
            self.assertEqual(r.status_code, 400, (bad, r.text))
        self.assertIsNone(self.client.post("/internal/workbench/claim", headers=self.worker,
                                           json={"worker_id": "w1"}).json()["job"])

    def test_deceptive_and_case_colliding_allow_lists_are_refused(self):
        for i, bad in enumerate((["calculаtor.py"],          # Cyrillic 'a' homoglyph
                                 ["calc​.py"],               # zero-width space
                                 ["calc‮.py"],               # right-to-left override
                                 ["café.py"], ["my file.py"],
                                 ["Calculator.py", "calculator.py"])):
            r = self.enqueue("deceptive-%d" % i, bad)
            self.assertEqual(r.status_code, 400, (ascii(bad), r.text))

    def test_case_variant_protected_paths_are_refused(self):
        # D9: .GIT/.GitHub/.GITATTRIBUTES/.GITMODULES are the protected files on a case-insensitive filesystem.
        for i, bad in enumerate((".GIT/config", "sub/.Git/HEAD", ".GitHub/workflows/ci.yml", ".GITATTRIBUTES",
                                 ".GITMODULES", "a/.GitModules")):
            r = self.enqueue("d9-%d" % i, [bad])
            self.assertEqual(r.status_code, 400, (bad, r.text))

    def test_patch_paths_that_are_deceptive_or_case_variants_are_rejected(self):
        for key, path in (("homo", "calculаtor.py"), ("case", "Calculator.py")):
            self.assertEqual(self.enqueue("patch-" + key, ["calculator.py"]).status_code, 200)
            job = self.claim()
            reply = self.report(job, [patch_for("calculator.py", path)])
            self.assertEqual(reply.json()["state"], "failed", (key, reply.text))

    def test_worker_receives_the_allow_list(self):
        self.assertEqual(self.enqueue("k1", ["calculator.py"]).status_code, 200)
        self.assertEqual(self.claim()["allowed_files"], ["calculator.py"])

    def test_execution_b_unauthorised_file_is_rejected_by_campus(self):
        self.assertEqual(self.enqueue("exec-b", ["calculator.py"]).status_code, 200)
        job = self.claim()
        reply = self.report(job, [patch_for("calculator.py", "test_calculator.py")])
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["state"], "failed")
        self.assertTrue(reply.json()["campus_rejected"])
        result = self.status(job)["result"]
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["worker_verdict"], "PASS")
        self.assertIn("Campus scope check: Unauthorised change: test_calculator.py", result["defects"])
        self.assertEqual(self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner,
                                          json={"decision": "accept"}).status_code, 409)
        self.assertEqual(self.client.get("/milo/executions/%s/artifacts/changes.diff" % job["order_id"],
                                         headers=self.owner).status_code, 409)

    def test_execution_a_authorised_files_reach_owner_approval(self):
        self.assertEqual(self.enqueue("exec-a", ["calc.py", "test_calc.py"]).status_code, 200)
        job = self.claim()
        reply = self.report(job, [patch_for("calc.py", "test_calc.py")])
        self.assertEqual(reply.json()["state"], "awaiting_approval", reply.text)
        self.assertTrue(self.status(job)["result"]["scope_verified"])
        diff_sha = next(a["sha256"] for a in self.status(job)["result"]["artifacts"] if a["name"] == "changes.diff")
        self.assertEqual(self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner,
                                          json={"decision": "accept", "patch_sha256": diff_sha}).status_code, 200)

    def test_pass_without_a_verifiable_patch_is_not_accepted(self):
        self.assertEqual(self.enqueue("no-patch", ["calc.py"]).status_code, 200)
        job = self.claim()
        self.assertEqual(self.report(job, [artifact("calc.py", "x")]).json()["state"], "failed")

    def test_patch_content_cannot_hide_or_fake_paths(self):
        self.assertEqual(self.enqueue("trick", ["calc.py"]).status_code, 200)
        job = self.claim()
        sneaky = patch_for("calc.py", body="+--- a/calc.py\n+diff --git a/calc.py b/calc.py\n") ["content"] + \
            "diff --git a/secrets.py b/secrets.py\nnew file mode 120000\n"
        reply = self.report(job, [artifact("changes.diff", sneaky)])
        self.assertEqual(reply.json()["state"], "failed")
        defects = " ".join(self.status(job)["result"]["defects"])
        self.assertIn("secrets.py", defects)

    def test_identical_replay_after_downgrade_stays_idempotent(self):
        self.assertEqual(self.enqueue("replay", ["calculator.py"]).status_code, 200)
        job = self.claim()
        files = [patch_for("calculator.py", "test_calculator.py")]
        self.assertEqual(self.report(job, files).json()["state"], "failed")
        again = self.report(job, files)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()["duplicate"])

    def test_results_recorded_before_c3_cannot_be_accepted(self):
        # Shape of the live calculator order: awaiting approval, PASS, no Campus scope check recorded.
        self.assertEqual(self.enqueue("legacy", ["calculator.py"]).status_code, 200)
        c = connect()
        c.execute("UPDATE workbench_orders SET state='awaiting_approval', allowed_json=NULL, result_json=? WHERE id=?",
                  (json.dumps({"verdict": "PASS", "execution_mode": "REAL", "evidence": ["ok"], "defects": [],
                               "artifacts": []}), "legacy"))
        c.commit()
        c.close()
        r = self.client.post("/milo/executions/legacy/decision", headers=self.owner, json={"decision": "accept"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("not verified", r.text)


class HonestModes(Orders):
    def test_none_mode_is_for_results_where_nothing_ran(self):
        self.assertEqual(self.enqueue("none-1", ["calc.py"]).status_code, 200)
        job = self.claim()
        self.assertEqual(self.report(job, [], verdict="PASS", mode="NONE").status_code, 400)
        self.assertEqual(self.report(job, [], verdict="PASS", mode="MADE_UP").status_code, 400)
        blocked = self.report(job, [], verdict="BLOCKED", mode="NONE")
        self.assertEqual(blocked.status_code, 200, blocked.text)
        self.assertEqual(self.status(job)["result"]["execution_mode"], "NONE")


class MissingAccessKey(Base):
    env_extra = {"AYVEN_API_TOKEN": None, "AYVEN_ACCESS_KEY": None, "AYVEN_ENV": "production", "AYVEN_LOCAL": None}

    def test_no_key_outside_local_mode_fails_closed(self):
        self.assertEqual(self.client.get("/state").status_code, 401)
        self.assertEqual(self.client.post("/milo/executions", json={}).status_code, 401)

    def test_explicit_local_mode_keeps_keyless_development(self):
        with patch.dict(os.environ, {"AYVEN_ENV": "local"}):
            self.assertNotEqual(self.client.get("/state").status_code, 401)


class McpApproval(unittest.TestCase):
    def test_actions_are_presented_with_their_real_approval_state(self):
        from apps.api.app.intelligence import mcp_boundary
        seen = []

        def fake_authorize(agent_id, cap, approved=False):
            seen.append(approved)
            return cap
        with patch.object(mcp_boundary, "sdk_installed", return_value=True), \
                patch.object(mcp_boundary, "authorize", fake_authorize), \
                patch.object(mcp_boundary, "configured_servers", return_value=[{"name": "s"}]):
            result = mcp_boundary.prepare_call("employee", "send_email", approved=False)
        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(seen, [False])


if __name__ == "__main__":
    unittest.main()
