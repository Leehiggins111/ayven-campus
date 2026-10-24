"""A Workbench PASS is a claim until Campus has checked the Workbench's SIGNED verification record of the exact
patch, for this order, revision and allow-list; simulated or sample work can never be approved.

Before (c8a61e4): Campus checked an unsigned, self-consistent record, so anyone holding the worker token could
post a fabricated diff + record + log and reach owner approval with tests_verified=true (audit 23 Oct, attack E).
"""
import json
from unittest.mock import patch as mock_env

from test_workbench_scope_http import Orders, patch_for
from workbench_signed import artifact, signed_evidence, PASS_LOG, TESTS


class TestEvidence(Orders):
    def submit(self, key, build, mode="REAL", allowed=("calc.py",)):
        """build(order_id) -> artifacts."""
        self.assertEqual(self.enqueue(key, list(allowed)).status_code, 200)
        job = self.claim()
        reply = self.report(job, build(job["order_id"]), mode=mode, evidence=False)
        self.assertEqual(reply.status_code, 200, reply.text)
        return job, reply.json(), self.status(job)["result"]

    def assert_refused(self, key, build, reason, **kw):
        job, reply, result = self.submit(key, build, **kw)
        self.assertEqual(reply["state"], "failed", result)
        self.assertFalse(result["tests_verified"])
        self.assertTrue(any(reason in d for d in result["defects"]), result["defects"])
        accept = self.decide("/milo/executions/%s/decision" % job["order_id"], {"decision": "accept"})
        self.assertEqual(accept.status_code, 409)
        return result

    def good(self, order_id, **kw):
        p = patch_for("calc.py")
        return [p] + signed_evidence(p, order_id, ["calc.py"], **kw)

    def test_signed_verification_reaches_approval(self):
        job, reply, result = self.submit("good", self.good)
        self.assertEqual(reply["state"], "awaiting_approval", result)
        self.assertTrue(result["tests_verified"] and result["scope_verified"])
        self.assertEqual(self.decide("/milo/executions/%s/decision" % job["order_id"], {"decision": "accept"}).status_code, 200)

    def test_a_bare_pass_claim_is_refused(self):
        self.assert_refused("bare", lambda o: [patch_for("calc.py")], "missing verification evidence")

    def test_forged_unsigned_record_is_refused(self):
        self.assert_refused("unsigned", lambda o: self.good(o, signed=False), "not signed")

    def test_record_signed_with_another_key_is_refused(self):
        self.assert_refused("otherkey", lambda o: self.good(o, key="z" * 48), "signature is invalid")

    def test_record_altered_after_signing_is_refused(self):
        def build(order_id):
            files = self.good(order_id, outcomes={t: "failed" for t in TESTS})
            env = json.loads(files[1]["content"])
            env["record"]["verified"]["outcomes"] = {t: "passed" for t in TESTS}   # flip FAIL -> PASS
            env["record"]["verified"]["exit_code"] = 0
            files[1] = artifact("qa-run.json", json.dumps(env))
            return files
        self.assert_refused("altered", build, "signature is invalid")

    def test_campus_without_an_attestation_key_refuses_everything(self):
        with mock_env.dict("os.environ", {"AYVEN_ATTESTATION_KEY": ""}):
            self.assert_refused("nokey", self.good, "no attestation key")

    def test_evidence_from_another_order_cannot_be_replayed(self):
        self.assert_refused("replay", lambda o: [patch_for("calc.py")] +
                            signed_evidence(patch_for("calc.py"), "some-other-order", ["calc.py"]), "different order")

    def test_evidence_from_an_earlier_revision_cannot_be_reused(self):
        self.assert_refused("rev", lambda o: self.good(o, revision=1), "different order or revision")

    def test_evidence_under_another_allow_list_is_refused(self):
        self.assert_refused("scope", lambda o: [patch_for("calc.py")] +
                            signed_evidence(patch_for("calc.py"), o, ["calc.py", "test_calc.py"]), "different allow-list")

    def test_a_cut_or_changed_log_is_refused(self):
        def build(order_id):
            files = self.good(order_id)
            files[2] = artifact("qa-test.log", PASS_LOG[: len(PASS_LOG) // 2])
            return files
        self.assert_refused("cut", build, "not byte-for-byte")

    def test_an_honest_failing_verification_is_refused(self):
        self.assert_refused("failing", lambda o: self.good(o, outcomes={t: "failed" for t in TESTS},
                                                            mutate=lambda r: r.update(verdict="FAIL",
                                                                                      reasons=["4 failing"])),
                            "did not pass")

    def test_tests_dropped_since_the_base_revision_are_refused(self):
        def drop(record):
            record["verified"]["collected"] = TESTS[:2]
            record["verified"]["outcomes"] = {t: "passed" for t in TESTS[:2]}
        self.assert_refused("dropped", lambda o: self.good(o, mutate=drop), "no longer run")

    def test_a_run_of_a_different_patch_is_refused(self):
        def build(order_id):
            files = self.good(order_id)
            files[0] = patch_for("calc.py", body="+y\n")
            return files
        self.assert_refused("other", build, "not run on this exact patch")

    def test_changed_files_must_match_the_patch(self):
        self.assert_refused("files", lambda o: self.good(o, mutate=lambda r: r.update(changed_files=[])),
                            "do not match the patch")

    def test_tamper_findings_are_refused(self):
        self.assert_refused("tamper", lambda o: self.good(o, mutate=lambda r: r.update(
            tamper_findings=["calc.py: exits the interpreter directly (os._exit)"])), "interferes")

    def test_results_recorded_before_this_check_cannot_be_accepted(self):
        self.assertEqual(self.enqueue("legacy-tests", ["calc.py"]).status_code, 200)
        from apps.api.app.db import connect
        c = connect()
        c.execute("UPDATE workbench_orders SET state='awaiting_approval', result_json=? WHERE id=?",
                  (json.dumps({"verdict": "PASS", "execution_mode": "REAL", "evidence": ["2 tests passed"],
                               "defects": [], "artifacts": [], "scope_verified": True}), "legacy-tests"))
        c.commit()
        c.close()
        r = self.decide("/milo/executions/legacy-tests/decision", {"decision": "accept"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("no verified review package", r.text)


class SampleWork(Orders):
    def test_simulated_work_is_never_approvable(self):
        self.assertEqual(self.enqueue("sample", ["calc.py"]).status_code, 200)
        job = self.claim()
        patch = patch_for("calc.py")
        reply = self.report(job, [patch] + signed_evidence(patch, job["order_id"], ["calc.py"]), mode="SIMULATED",
                            evidence=False)
        self.assertEqual(reply.json()["state"], "simulated", reply.text)
        for decision in ({"decision": "accept"}, {"decision": "reject", "reason": "sample"}):
            r = self.decide("/milo/executions/%s/decision" % job["order_id"], decision)
            self.assertEqual(r.status_code, 409, (decision, r.text))
        self.assertEqual(self.status(job)["state"], "simulated")
        self.assertFalse(self.status(job)["owner_accepted"])
        self.assertEqual(self.client.get("/milo/executions/%s/artifacts/changes.diff" % job["order_id"],
                                         headers=self.owner).status_code, 409)
        milo = self.client.get("/milo/jobs/" + self.status(job)["project_id"], headers=self.owner).json()
        self.assertFalse(milo["waiting_for_lee"])
        self.assertIsNone(milo["approval"])
