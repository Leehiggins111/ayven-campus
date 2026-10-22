"""Q&T findings 1, 4 and 5 against the real Campus app: a Workbench PASS is a claim until Campus has checked
the Workbench server's own test-run record, its complete log and the patch it was run on; simulated or sample
work can never be approved.

Before: Campus accepted verdict PASS / execution_mode REAL with free-text evidence and only a scope check, so a
bare, truncated or mismatched test claim reached owner approval; a SIMULATED PASS sat in awaiting_approval.
"""
import json

from test_workbench_scope_http import Orders, artifact, evidence_for, patch_for

FAILING_LOG = ("test_add (test_calc.T.test_add) ... FAIL\n\nFAIL: test_add (test_calc.T.test_add)\n" + "-" * 70 +
               "\nRan 2 tests in 0.004s\n\nFAILED (failures=1)\n")


class TestEvidence(Orders):
    def submit(self, key, files, mode="REAL"):
        self.assertEqual(self.enqueue(key, ["calc.py"]).status_code, 200)
        job = self.claim()
        reply = self.report(job, files, mode=mode, evidence=False)
        self.assertEqual(reply.status_code, 200, reply.text)
        return job, reply.json(), self.status(job)["result"]

    def assert_refused(self, key, files, reason):
        job, reply, result = self.submit(key, files)
        self.assertEqual(reply["state"], "failed", result)
        self.assertFalse(result["tests_verified"])
        self.assertTrue(any(reason in d for d in result["defects"]), result["defects"])
        accept = self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner,
                                  json={"decision": "accept"})
        self.assertEqual(accept.status_code, 409)
        return result

    def test_verified_test_run_reaches_approval(self):
        patch = patch_for("calc.py")
        job, reply, result = self.submit("good", [patch] + evidence_for(patch))
        self.assertEqual(reply["state"], "awaiting_approval", result)
        self.assertTrue(result["tests_verified"] and result["scope_verified"])
        self.assertEqual(self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner,
                                          json={"decision": "accept"}).status_code, 200)

    def test_a_bare_pass_claim_is_refused(self):
        self.assert_refused("bare", [patch_for("calc.py")], "missing test evidence")

    def test_a_cut_or_changed_log_is_refused(self):
        patch = patch_for("calc.py")
        record, log = evidence_for(patch)
        cut = log["content"][len(log["content"]) // 2:]
        self.assert_refused("cut", [patch, record, artifact("qa-test.log", cut)], "not the complete log")

    def test_failing_tests_are_refused_even_if_the_record_says_ok(self):
        patch = patch_for("calc.py")
        claimed_ok = evidence_for(patch, log=FAILING_LOG)    # the record claims OK; the complete log says otherwise
        result = self.assert_refused("failing", [patch] + claimed_ok, "does not end with unittest's OK line")
        self.assertIn("names failing tests", " ".join(result["defects"]))

    def test_nonzero_exit_or_zero_tests_is_refused(self):
        patch = patch_for("calc.py")
        self.assert_refused("exit", [patch] + evidence_for(patch, exit_code=1), "exited 1")
        self.assert_refused("zero", [patch] + evidence_for(patch, tests=0), "No tests ran")

    def test_a_run_of_a_different_patch_is_refused(self):
        tested, exported = patch_for("calc.py"), patch_for("calc.py", body="+y\n")
        self.assert_refused("other", [exported] + evidence_for(tested), "not run on this exact patch")

    def test_results_recorded_before_this_check_cannot_be_accepted(self):
        self.assertEqual(self.enqueue("legacy-tests", ["calc.py"]).status_code, 200)
        from apps.api.app.db import connect
        c = connect()
        c.execute("UPDATE workbench_orders SET state='awaiting_approval', result_json=? WHERE id=?",
                  (json.dumps({"verdict": "PASS", "execution_mode": "REAL", "evidence": ["2 tests passed"],
                               "defects": [], "artifacts": [], "scope_verified": True}), "legacy-tests"))
        c.commit()
        c.close()
        r = self.client.post("/milo/executions/legacy-tests/decision", headers=self.owner, json={"decision": "accept"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("test run", r.text)


class SampleWork(Orders):
    def test_simulated_work_is_never_approvable(self):
        self.assertEqual(self.enqueue("sample", ["calc.py"]).status_code, 200)
        job = self.claim()
        patch = patch_for("calc.py")
        reply = self.report(job, [patch] + evidence_for(patch), mode="SIMULATED", evidence=False)
        self.assertEqual(reply.json()["state"], "simulated", reply.text)
        for decision in ({"decision": "accept"}, {"decision": "reject", "reason": "sample"}):
            r = self.client.post("/milo/executions/%s/decision" % job["order_id"], headers=self.owner, json=decision)
            self.assertEqual(r.status_code, 409, (decision, r.text))
        self.assertEqual(self.status(job)["state"], "simulated")
        self.assertFalse(self.status(job)["owner_accepted"])
        self.assertEqual(self.client.get("/milo/executions/%s/artifacts/changes.diff" % job["order_id"],
                                         headers=self.owner).status_code, 409)
        milo = self.client.get("/milo/jobs/" + self.status(job)["project_id"], headers=self.owner).json()
        self.assertFalse(milo["waiting_for_lee"])
        self.assertIsNone(milo["approval"])
