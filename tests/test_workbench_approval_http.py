"""Exact-version owner approval against the real Campus app (verification v2, review packages, signed approvals,
the apply queue). Each test drives the HTTP routes Milo and the pull worker use."""
import hashlib
import hmac
import json

from apps.api.app.db import connect
from apps.api.app.workbench_evidence import canonical, key_id, request_key
from apps.api.app.workbench_scope import ScopeError, check_path, verify_patch
from test_workbench_scope_http import Orders, patch_for
from workbench_signed import ACCEPTANCE, KEY, signed_evidence


class Approval(Orders):
    def order(self, key="ap", **extra):
        body = {"objective": "Add multiply", "project_alias": "cloud-demo", "acceptance_criteria": ["tests pass"],
                "job_cap_gbp": 0, "allowed_files": ["calc.py"], **extra}
        return self.client.post("/milo/executions", headers={**self.owner, "Idempotency-Key": key}, json=body)

    def post(self, job, verdict="PASS", artifacts=None, **extra):
        return self.client.post("/internal/workbench/%s/result" % job["order_id"], headers=self.worker,
                                json={"worker_id": "w1", "fence": job["fence"], "verdict": verdict,
                                      "execution_mode": "REAL" if verdict != "CLARIFY" else "NONE",
                                      "evidence": ["verified"] if verdict == "PASS" else [],
                                      "defects": [] if verdict in ("PASS", "CLARIFY") else ["tests fail"],
                                      "artifacts": artifacts or [], **extra})

    def evidence(self, job, revision=0, **kw):
        p = patch_for("calc.py")
        return [p] + signed_evidence(p, job["order_id"], ["calc.py"], revision=revision, **kw)

    def verified(self, key="ap", order_extra=None, **kw):
        self.assertEqual(self.order(key, **(order_extra or {})).status_code, 200)
        job = self.claim()
        r = self.post(job, artifacts=self.evidence(job, **kw))
        self.assertEqual(r.json()["state"], "awaiting_approval", r.text)
        return job

    def review(self, order_id):
        return self.client.get("/milo/executions/%s/review" % order_id, headers=self.owner)

    def decision(self, order_id, decision="accept", reason="", headers=None, **body):
        review = self.review(order_id).json()
        payload = {"decision": decision, "reason": reason, "package_sha256": review.get("package_sha256"),
                   "revision": review.get("revision"), **body}
        return self.client.post("/milo/executions/%s/decision" % order_id, headers={**self.owner, **(headers or {})},
                                json=payload)

    def apply(self, order_id, sha=None):
        sha = sha or self.review(order_id).json()["package_sha256"]
        return self.client.post("/milo/executions/%s/apply" % order_id, headers=self.owner, json={"package_sha256": sha})

    def claim_apply(self, worker="w1"):
        return self.client.post("/internal/workbench/apply/claim", headers=self.worker,
                                json={"worker_id": worker}).json()["apply"]

    def applied(self, claimed, outcome="applied", commit="c" * 40, worker="w1", detail=""):
        return self.client.post("/internal/workbench/apply/%s/result" % claimed["order_id"], headers=self.worker,
                                json={"worker_id": worker, "fence": claimed["fence"], "outcome": outcome,
                                      "commit": commit if outcome == "applied" else None, "detail": detail})

    def sql(self, statement, *args):
        c = connect()
        c.execute(statement, args)
        c.commit()
        c.close()

    # ---------------------------------------------------------------- the review package
    def test_review_package_binds_base_patch_allow_list_artifacts_and_coverage(self):
        job = self.verified()
        r = self.review("ap")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        package = body["package"]
        self.assertEqual(body["package_sha256"], hashlib.sha256(canonical(package)).hexdigest())
        self.assertTrue(body["intact"])
        record = json.loads(next(a["content"] for a in self.evidence(job) if a["name"] == "qa-run.json"))["record"]
        self.assertEqual(package["base_revision"], record["base_revision"])
        self.assertEqual(package["patch_sha256"], record["patch_sha256"])
        self.assertEqual(package["allowed_files"], ["calc.py"])
        self.assertEqual(package["request_key"], request_key("ap", 0))
        self.assertEqual(package["revision"], 0)
        self.assertEqual(set(package["artifacts"]), {"changes.diff", "qa-run.json", "qa-test.log", "baseline-test.log",
                                                     "regression-test.log", "acceptance-test.log",
                                                     "acceptance-baseline-test.log"})
        self.assertEqual(package["coverage"]["level"], "regression-only")
        status = self.status(job)
        self.assertEqual(status["review"]["package_sha256"], body["package_sha256"])
        for field in ("acceptance_criteria", "constraints", "job_cap_gbp", "claimed_at", "last_heartbeat_at",
                      "finished_at", "worker_id"):        # Milo D3/D4
            self.assertIn(field, status)
        self.assertEqual(status["worker_id"], "w1")
        self.assertIsNotNone(status["finished_at"])

    def test_no_package_until_a_verified_pass(self):
        self.order()
        self.assertEqual(self.review("ap").status_code, 409)
        job = self.claim()
        self.post(job, "FAIL", artifacts=[])
        self.assertEqual(self.review("ap").status_code, 409)

    # ---------------------------------------------------------------- decisions
    def test_decision_must_name_the_reviewed_package_and_revision(self):
        self.verified()
        url = "/milo/executions/ap/decision"
        sha = self.review("ap").json()["package_sha256"]
        self.assertEqual(self.client.post(url, headers=self.owner, json={"decision": "accept"}).status_code, 400)
        wrong = self.client.post(url, headers=self.owner, json={"decision": "accept", "package_sha256": "f" * 64,
                                                                "revision": 0})
        self.assertEqual(wrong.status_code, 409)
        self.assertIn("not the version you reviewed", wrong.text)
        stale = self.client.post(url, headers=self.owner, json={"decision": "accept", "package_sha256": sha,
                                                                "revision": 1})
        self.assertEqual(stale.status_code, 409)
        self.assertIn("revision", stale.text)
        self.assertEqual(self.status({"order_id": "ap"})["state"], "awaiting_approval")
        ok = self.client.post(url, headers=self.owner, json={"decision": "accept", "package_sha256": sha, "revision": 0})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["state"], "completed")

    def test_accepting_signs_an_approval_binding_order_revision_base_patch_files_and_owner(self):
        self.verified()
        r = self.decision("ap", headers={"X-Ayven-Owner": "owner@example.invalid"})
        self.assertEqual(r.status_code, 200, r.text)
        c = connect()
        row = c.execute("SELECT decision_json, package_json, package_sha256 FROM workbench_orders WHERE id='ap'").fetchone()
        stored = c.execute("SELECT * FROM workbench_decisions WHERE order_id='ap'").fetchall()
        c.close()
        decision, package = json.loads(row["decision_json"]), json.loads(row["package_json"])
        approval = decision["approval"]
        expected = hmac.new(KEY.encode(), canonical(approval["record"]), hashlib.sha256).hexdigest()
        self.assertEqual(approval["signature"], {"alg": "HMAC-SHA256", "key_id": key_id(KEY.encode()),
                                                 "value": expected})
        record = approval["record"]
        self.assertEqual((record["schema"], record["decision"], record["order_id"], record["revision"]),
                         ("ayven.approval/v1", "accept", "ap", 0))
        self.assertEqual(record["package_sha256"], row["package_sha256"])
        self.assertEqual(record["patch_sha256"], package["patch_sha256"])
        self.assertEqual(record["base_revision"], package["base_revision"])
        self.assertEqual(record["allowed_files"], ["calc.py"])
        self.assertEqual(record["request_key"], request_key("ap", 0))
        self.assertEqual(record["owner"], {"principal": "bearer", "owner": "owner@example.invalid"})
        self.assertEqual(len(stored), 1)                       # durable, append-only decision record
        self.assertIn("decision", [e["kind"] for e in self.client.get("/milo/executions/ap/events",
                                                                       headers=self.owner).json()["events"]])

    def test_a_repeated_decision_is_idempotent_and_a_decision_cannot_be_changed(self):
        self.verified()
        first = self.decision("ap").json()
        again = self.decision("ap")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(again.json()["approval_id"], first["approval_id"])
        flip = self.decision("ap", "reject", reason="changed my mind")
        self.assertEqual(flip.status_code, 409)
        self.assertEqual(self.status({"order_id": "ap"})["state"], "completed")

    def test_the_rejection_reason_is_stored_and_returned(self):
        self.verified()
        r = self.decision("ap", "reject", reason="Deletes add()")
        self.assertEqual(r.json()["state"], "rejected")
        self.assertEqual(self.status({"order_id": "ap"})["decision"]["reason"], "Deletes add()")    # Milo D5

    def test_a_package_from_an_earlier_revision_cannot_be_approved(self):
        self.verified()
        old = self.review("ap").json()["package_sha256"]
        self.decision("ap", "reject", reason="needs rework")
        r = self.client.post("/milo/executions/ap/revise", headers=self.owner, json={"kind": "rework", "text": "fix"})
        self.assertEqual(r.json()["revision"], 1)
        job = self.claim()
        self.assertEqual(self.post(job, artifacts=self.evidence(job, revision=1)).json()["state"], "awaiting_approval")
        new = self.review("ap").json()
        self.assertNotEqual(new["package_sha256"], old)
        self.assertEqual(new["revision"], 1)
        for sha, revision in ((old, 0), (old, 1), (new["package_sha256"], 0)):
            r = self.client.post("/milo/executions/ap/decision", headers=self.owner,
                                 json={"decision": "accept", "package_sha256": sha, "revision": revision})
            self.assertEqual(r.status_code, 409, (sha, revision))
        self.assertEqual(self.decision("ap").status_code, 200)

    def test_changed_stored_artifacts_block_the_decision_and_the_apply(self):
        self.verified()
        self.sql("UPDATE workbench_artifacts SET content=? WHERE order_id='ap' AND name='changes.diff'",
                 b"diff --git a/calc.py b/calc.py\n+evil\n")
        self.assertFalse(self.review("ap").json()["intact"])
        r = self.decision("ap")
        self.assertEqual(r.status_code, 409)
        self.assertIn("no longer match", r.text)

    def test_worker_tests_only_needs_an_explicit_acknowledgement(self):
        self.verified(mutate=lambda rec: rec["coverage"].update(level="worker-tests-only"))
        r = self.decision("ap")
        self.assertEqual(r.status_code, 409)
        self.assertIn("acknowledge_coverage", r.text)
        r = self.decision("ap", acknowledge_coverage=True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.status({"order_id": "ap"})["decision"]["acknowledged_coverage"])

    # ---------------------------------------------------------------- apply
    def test_a_rejected_result_is_never_applied(self):
        self.verified()
        self.decision("ap", "reject", reason="no")
        r = self.apply("ap")
        self.assertEqual(r.status_code, 409)
        self.assertIn("never applied", r.text)
        self.assertIsNone(self.claim_apply())

    def test_nothing_is_applied_before_acceptance(self):
        self.verified()
        r = self.apply("ap")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Accept the result", r.text)
        self.assertIsNone(self.claim_apply())

    def test_apply_carries_exactly_the_accepted_bytes_once(self):
        job = self.verified()
        self.decision("ap")
        sha = self.review("ap").json()["package_sha256"]
        self.assertEqual(self.apply("ap", "e" * 64).status_code, 409)     # not the accepted package
        r = self.apply("ap", sha)
        self.assertEqual(r.json(), {"apply_state": "requested", "duplicate": False})
        self.assertTrue(self.apply("ap", sha).json()["duplicate"])
        claimed = self.claim_apply()
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.claim_apply("w2"))                         # one holder at a time
        diff = next(a["content"] for a in self.evidence(job) if a["name"] == "changes.diff")
        self.assertEqual(claimed["patch"], diff)
        self.assertEqual(hashlib.sha256(claimed["patch"].encode()).hexdigest(),
                         claimed["approval"]["record"]["patch_sha256"])
        self.assertEqual(claimed["record"]["record"]["patch_sha256"], claimed["approval"]["record"]["patch_sha256"])
        self.assertEqual(self.applied(dict(claimed, fence=claimed["fence"] + 1)).status_code, 409)   # stale fence
        self.assertEqual(self.applied(claimed, commit="xyz").status_code, 400)
        done = self.applied(claimed)
        self.assertEqual(done.json()["apply_state"], "applied", done.text)
        self.assertTrue(self.applied(claimed).json()["duplicate"])          # replayed result
        self.assertEqual(self.applied(claimed, commit="d" * 40).status_code, 409)
        again = self.apply("ap", sha).json()
        self.assertEqual((again["apply_state"], again["duplicate"]), ("applied", True))
        self.assertIsNone(self.claim_apply())
        view = self.status({"order_id": "ap"})["apply"]
        self.assertEqual((view["state"], view["outcome"]["commit"]), ("applied", "c" * 40))
        kinds = [e["kind"] for e in self.client.get("/milo/executions/ap/events", headers=self.owner).json()["events"]]
        self.assertEqual(kinds[-3:], ["apply_requested", "apply_claimed", "applied"])

    def test_a_lost_apply_worker_is_replaced_then_gives_up(self):
        self.verified()
        self.decision("ap")
        self.apply("ap")
        fences = []
        for _ in range(3):
            claimed = self.claim_apply()
            fences.append(claimed["fence"])
            self.sql("UPDATE workbench_applies SET lease_until=1 WHERE order_id='ap'")
        self.assertIsNone(self.claim_apply())
        self.assertEqual(fences, [1, 2, 3])
        self.assertEqual(self.status({"order_id": "ap"})["apply"]["state"], "failed")
        self.assertEqual(self.apply("ap").json()["apply_state"], "requested")      # the owner may ask again

    def test_a_refused_apply_is_recorded_and_can_be_requested_again(self):
        self.verified()
        self.decision("ap")
        self.apply("ap")
        claimed = self.claim_apply()
        self.assertEqual(self.applied(claimed, "refused", detail="The project has moved").json()["apply_state"], "refused")
        view = self.status({"order_id": "ap"})["apply"]
        self.assertEqual((view["state"], view["outcome"]["detail"]), ("refused", "The project has moved"))
        self.assertEqual(self.apply("ap").json()["apply_state"], "requested")
        self.assertEqual(self.claim_apply()["fence"], claimed["fence"] + 1)

    def test_a_changed_artifact_after_acceptance_is_not_handed_to_the_worker(self):
        self.verified()
        self.decision("ap")
        self.apply("ap")
        self.sql("UPDATE workbench_artifacts SET content=? WHERE order_id='ap' AND name='changes.diff'", b"tampered")
        self.assertIsNone(self.claim_apply())
        self.assertEqual(self.status({"order_id": "ap"})["apply"]["state"], "refused")

    def test_owner_and_worker_routes_stay_separate(self):
        self.verified()
        for method, path, body in (("get", "/milo/executions/ap/review", None),
                                   ("post", "/milo/executions/ap/apply", {"package_sha256": "a" * 64}),
                                   ("post", "/milo/executions/ap/clarification", {"answer": "x"})):
            r = getattr(self.client, method)(path, headers=self.worker, **({"json": body} if body else {}))
            self.assertEqual(r.status_code, 401, path)
        self.assertEqual(self.client.post("/internal/workbench/apply/claim", headers=self.owner,
                                          json={"worker_id": "x"}).status_code, 401)

    # ---------------------------------------------------------------- verification v2 evidence
    def assert_failed(self, key, contains, order_extra=None, **kw):
        self.assertEqual(self.order(key, **(order_extra or {})).status_code, 200)
        job = self.claim()
        r = self.post(job, artifacts=self.evidence(job, **kw))
        self.assertEqual(r.json()["state"], "failed", r.text)
        defects = " ".join(self.status(job)["result"]["defects"])
        self.assertIn(contains, defects)
        self.assertEqual(self.review(key).status_code, 409)

    def test_original_tests_failing_against_the_patch_are_refused(self):
        self.assert_failed("reg", "Original tests fail", mutate=lambda r: r["regression"]["outcomes"].update(
            {"test_pricing.py::test_0": "failed"}))

    def test_a_record_without_the_original_tests_run_is_refused(self):
        self.assert_failed("noreg", "no run of the original tests", mutate=lambda r: r.update(regression=None))

    def test_removed_definitions_are_refused_unless_the_order_allows_them(self):
        removed = lambda r: r.update(removed_definitions=["calc.py::add"])   # noqa: E731
        self.assert_failed("rm", "removes existing definitions", mutate=removed)
        self.verified("rm-ok", order_extra={"allowed_removals": ["calc.py::add"]}, allowed_removals=["calc.py::add"],
                      mutate=removed)

    def test_exemptions_in_the_record_must_be_the_orders(self):
        self.assert_failed("ex", "different allowed removals", allowed_removals=["calc.py::add"])
        self.assert_failed("sup", "different superseded tests", superseded=["test_pricing.py::test_0"])

    def test_acceptance_tests_must_be_the_orders_and_all_pass(self):
        self.verified("acc", order_extra={"acceptance_tests": ACCEPTANCE}, acceptance=ACCEPTANCE)
        self.assertEqual(self.review("acc").json()["package"]["coverage"]["level"], "owner-acceptance")
        self.assert_failed("acc-missing", "this order's acceptance tests", order_extra={"acceptance_tests": ACCEPTANCE})
        other = [{"path": "test_owner.py", "content": "def test_a():\n    pass\n"}]
        self.assert_failed("acc-other", "this order's acceptance tests", order_extra={"acceptance_tests": ACCEPTANCE},
                           acceptance=other)
        self.assert_failed("acc-fail", "Owner acceptance tests fail", order_extra={"acceptance_tests": ACCEPTANCE},
                           acceptance=ACCEPTANCE,
                           mutate=lambda r: r["acceptance"]["outcomes"].update({"test_owner.py::test_a": "failed"}))

    def test_a_log_with_no_matching_run_is_refused(self):
        self.assert_failed("log", "has content but the record has no acceptance run", acceptance=ACCEPTANCE,
                           mutate=lambda r: r.update(acceptance=None, acceptance_baseline=None, acceptance_tests=[]))

    def test_acceptance_tests_are_validated_and_reach_the_worker_but_not_the_brief(self):
        for bad, why in (([{"path": "calc.py", "content": "x"}], "test file"),
                         ([{"path": "test_calc.py", "content": "x"}], "allowed-files"),
                         ([{"path": "conftest.py", "content": "x"}], "test file"),
                         ([{"path": "../test_x.py", "content": "x"}], "path")):
            allowed = ["calc.py", "test_calc.py"]
            r = self.order("bad-" + str(len(why)) + bad[0]["path"].replace("/", "_"), acceptance_tests=bad,
                           allowed_files=allowed)
            self.assertEqual(r.status_code, 400, (bad, r.text))
            self.assertIn(why, r.text)
        self.assertEqual(self.order("acc", acceptance_tests=ACCEPTANCE, allowed_removals=["calc.py::add"]).status_code, 200)
        job = self.claim()
        self.assertEqual(job["acceptance_tests"], ACCEPTANCE)
        self.assertEqual(job["allowed_removals"], ["calc.py::add"])
        self.assertNotIn("assert True", job["objective"])
        self.assertEqual(self.status(job)["acceptance_tests"], [{"path": "test_owner.py"}])   # owners see paths only

    # ---------------------------------------------------------------- Milo's clarification route (D1)
    def test_answering_a_question_resumes_the_same_order(self):
        self.order("q", allow_clarification=True)
        job = self.claim()
        self.post(job, "CLARIFY", question="Floats too?")
        self.assertEqual(self.client.post("/milo/executions/q/clarification", headers=self.owner,
                                          json={"answer": "  "}).status_code, 400)
        r = self.client.post("/milo/executions/q/clarification", headers=self.owner, json={"answer": "Integers only"})
        self.assertEqual(r.json(), {"state": "queued", "revision": 1, "duplicate": False})
        again = self.client.post("/milo/executions/q/clarification", headers=self.owner, json={"answer": "x"})
        self.assertEqual(again.status_code, 409)
        job = self.claim()
        self.assertEqual(job["order_id"], "q")
        self.assertEqual(job["revisions"][-1]["question"], "Floats too?")
        self.assertEqual(job["revisions"][-1]["text"], "Integers only")

    # ---------------------------------------------------------------- credentials and paths
    def test_a_worker_token_equal_to_the_owner_key_is_refused(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"AYVEN_WORKER_TOKEN": os.environ["AYVEN_API_TOKEN"]}):
            r = self.client.post("/internal/workbench/claim", headers=self.owner, json={"worker_id": "x"})
        self.assertEqual(r.status_code, 401)

    def test_case_variant_git_paths_are_prohibited(self):
        for path in (".GIT/config", ".Git/hooks/pre-commit", "src/.GIT/config", ".git./config", ".GITMODULES",
                     ".GitAttributes", ".GITHUB/workflows/x.yml"):
            with self.assertRaises(ScopeError, msg=path):
                check_path(path)
            r = self.order("p" + hashlib.sha256(path.encode()).hexdigest()[:8], allowed_files=[path])
            self.assertEqual(r.status_code, 400, path)
        diff = {"changes.diff": b"diff --git a/.GIT/config b/.GIT/config\n--- a/.GIT/config\n+++ b/.GIT/config\n"}
        self.assertTrue(any("Unsafe path" in p for p in verify_patch([".GIT/config"], diff)))
