"""First-release Campus lane: approved-plan prototype orders, independent review, Lee's fix decision, teams.

Real FastAPI app and temporary database. The Alba door calculator is TEST DATA (tests/fixtures/alba). Results
are signed exactly as Workbench signs ayven.prototype/v1 records. Browser reviews use Campus's real runner in
headless Chromium when AYVEN_REVIEW_PYTHON points at an interpreter with Playwright; otherwise those tests are
skipped (never faked). Logic tests use a deterministic stand-in browser and say so.
"""
import copy
import hashlib
import hmac
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.api.app import prototype_review as pr
from apps.api.app.db import connect
from apps.api.app.workbench_evidence import canonical, key_id
from test_workbench_scope_http import Base, artifact
from workbench_signed import KEY

FIX = Path(__file__).resolve().parent / "fixtures" / "alba"
PLAN = json.loads((FIX / "plan.json").read_text(encoding="utf-8"))
GOOD = {n: (FIX / "prototype" / n).read_text(encoding="utf-8") for n in pr.FILES}
BROKEN = dict(GOOD, **{"app.js": (FIX / "broken-app.js").read_text(encoding="utf-8")})
READ = "r" * 40


def approval(plan=PLAN, **over):
    return {"approval_id": "appr-alba-test", "plan_id": plan["plan_id"], "revision": plan["revision"],
            "plan_sha256": pr.plan_sha256(plan), "decision": "approved", "approved_by": "owner (TEST DATA)", **over}


def signed_result(job, files, *, mode="FIXTURE", worker_mode=None, tamper=None, verdict="PASS"):
    """What the Workbench worker posts: files + a Workbench-signed ayven.prototype/v1 record."""
    record = {"schema": pr.RECORD_SCHEMA, "build_id": "b" * 24, "request_key": job["request_key"],
              "plan_id": job["plan"]["plan_id"], "plan_revision": job["plan"]["revision"],
              "plan_sha256": job["plan_sha256"], "approval_id": job["approval"]["approval_id"], "verdict": verdict,
              "execution_mode": mode, "files": {n: hashlib.sha256(files[n].encode()).hexdigest() for n in pr.FILES},
              "tests": {"passed": verdict == "PASS", "checks": []},
              "model_provenance": {"kind": "fixture", "problems": ["source supplied by a test fixture"]}}
    envelope = {"record": record, "signature": {"alg": "HMAC-SHA256", "key_id": key_id(KEY.encode()),
                                                "value": hmac.new(KEY.encode(), canonical(record),
                                                                  hashlib.sha256).hexdigest()}}
    sent = dict(files, **(tamper or {}))
    arts = [artifact(n, sent[n]) for n in pr.FILES] + [artifact(pr.RECORD, json.dumps(envelope))]
    return {"worker_id": "w1", "fence": job["fence"], "verdict": verdict, "execution_mode": worker_mode or mode,
            "evidence": [f"{j['requirement']} @ 1280px: passed" for j in job["plan"]["prototype"]["journeys"]],
            "defects": [] if verdict == "PASS" else ["build failed"], "artifacts": arts}


def review_python():
    python = os.environ.get("AYVEN_REVIEW_PYTHON")
    try:
        return python if python and subprocess.run([python, "-c", "import playwright"], capture_output=True,
                                                   timeout=30).returncode == 0 else None
    except OSError:
        return None


def stand_in_browser(pass_if):
    """Deterministic stand-in: passes each journey for which pass_if(requirement) is True; no-JS copy fails."""
    def run(files, journeys, mutant=False):
        checks = [{"requirement": j["requirement"], "width": w, "passed": (not mutant) and pass_if(j["requirement"]),
                   **({} if (not mutant) and pass_if(j["requirement"]) else {"error": "AssertionError: stand-in"})}
                  for w in pr.WIDTHS for j in journeys]
        ok = all(c["passed"] for c in checks)
        return {"passed": ok, "status": "ok" if ok else "failed", "checks": checks, "errors": []}
    return run


class Prototypes(Base):
    env_extra = {"AYVEN_PROTOTYPE_AUTO_REVIEW": "0", "AYVEN_READ_TOKEN": READ}

    def enqueue(self, plan=PLAN, appr=None, headers=None):
        return self.client.post("/ayven/prototypes", headers=headers or self.owner,
                                json={"plan": plan, "approval": appr or approval(plan)})

    def claim(self):
        return self.client.post("/internal/workbench/claim", headers=self.worker, json={"worker_id": "w1"}).json()["job"]

    def post(self, job, body):
        return self.client.post(f"/internal/workbench/{job['order_id']}/result", headers=self.worker, json=body)

    def status(self, order_id):
        return self.client.get(f"/ayven/prototypes/{order_id}", headers=self.owner).json()

    def built(self, files=GOOD, **kw):
        order = self.enqueue().json()
        job = self.claim()
        r = self.post(job, signed_result(job, files, **kw))
        self.assertEqual(r.status_code, 200, r.text)
        return order["order_id"], job


class ApprovedPlanOnly(Prototypes):
    def test_unapproved_or_mismatched_plan_is_refused_and_nothing_is_queued(self):
        self.assertEqual(self.enqueue(appr=approval(decision="pending")).status_code, 409)
        edited = copy.deepcopy(PLAN)
        edited["features"][0] = "Six-panel doors (£80 each)"
        self.assertEqual(self.enqueue(plan=edited, appr=approval(PLAN)).status_code, 409)
        self.assertEqual(self.enqueue(appr=approval(revision=2)).status_code, 409)
        self.assertIsNone(self.claim())

    def test_list_shows_each_prototype_with_the_single_view_shape(self):
        order_id = self.enqueue().json()["order_id"]
        listed = self.client.get("/ayven/prototypes", headers={"Authorization": "Bearer " + READ}).json()["prototypes"]
        self.assertEqual([p["order_id"] for p in listed], [order_id])
        self.assertEqual(listed[0], self.status(order_id))

    def test_read_only_credential_cannot_queue(self):
        self.assertEqual(self.enqueue(headers={"Authorization": "Bearer " + READ}).status_code, 401)

    def test_enqueue_is_idempotent_per_job_revision(self):
        first, again = self.enqueue().json(), self.enqueue().json()
        self.assertEqual((first["duplicate"], again["duplicate"]), (False, True))
        self.assertEqual(first["order_id"], again["order_id"])
        self.assertEqual(first["request_key"], "job-alba-doors-test:plan-rev-1")
        other = copy.deepcopy(PLAN)
        other["features"].append("Extra")
        self.assertEqual(self.enqueue(plan=other).status_code, 409)
        self.assertIsNotNone(self.claim())
        self.assertIsNone(self.claim())

    def test_claim_sends_the_plan_without_the_reviewers_held_out_checks(self):
        self.enqueue()
        job = self.claim()
        self.assertEqual(job["kind"], "prototype")
        self.assertNotIn("review", job["plan"])
        self.assertEqual(job["plan_sha256"], pr.plan_sha256(job["plan"]))
        self.assertEqual(job["approval"]["plan_sha256"], job["plan_sha256"])
        self.assertEqual(job["approved_plan_sha256"], pr.plan_sha256(PLAN))

    def test_lease_retry_keeps_the_same_build_identity(self):
        self.enqueue()
        first = self.claim()
        c = connect()
        c.execute("UPDATE workbench_orders SET lease_until=0 WHERE id=?", (first["order_id"],))
        c.commit()
        c.close()
        second = self.claim()
        self.assertEqual((second["order_id"], second["request_key"]), (first["order_id"], first["request_key"]))
        self.assertEqual(second["fence"], first["fence"] + 1)
        self.assertEqual(self.post(first, signed_result(first, GOOD)).status_code, 409)   # stale lease refused
        self.assertEqual(self.post(second, signed_result(second, GOOD)).json()["state"], "reviewing")


class Integrity(Prototypes):
    def test_tampered_file_fails_and_waits_for_lee(self):
        order_id, _ = self.built(tamper={"app.js": GOOD["app.js"].replace("85", "80")})
        s = self.status(order_id)
        self.assertEqual(s["state"], "failed")
        self.assertTrue(s["decision_required"])
        self.assertIn("app.js does not match", " ".join(s["findings"]["failed"]))
        self.assertIsNone(self.claim())          # nothing rebuilt without Lee

    def test_fixture_is_never_labelled_real(self):
        order_id, _ = self.built(worker_mode="REAL")      # the worker over-claims; the signed record says FIXTURE
        pr.review(order_id, browser=stand_in_browser(lambda _r: True))
        s = self.status(order_id)
        self.assertEqual(s["execution_mode"], "FIXTURE")
        self.assertEqual(s["review"]["label"], "TEST FIXTURE source, not a model build")

    def test_prototype_file_is_private_until_reviewed(self):
        order_id, _ = self.built()
        self.assertEqual(self.client.get(f"/ayven/prototypes/{order_id}/prototype/index.html",
                                         headers=self.owner).status_code, 409)
        pr.review(order_id, browser=stand_in_browser(lambda _r: True))
        page = self.client.get(f"/ayven/prototypes/{order_id}/prototype/index.html", headers=self.owner)
        self.assertEqual(page.status_code, 200)
        self.assertIn("connect-src 'none'", page.headers["content-security-policy"])
        self.assertEqual(self.client.get(f"/ayven/prototypes/{order_id}/prototype/index.html").status_code, 401)
        self.assertEqual(self.client.get(f"/ayven/prototypes/{order_id}/prototype/secrets.txt",
                                         headers=self.owner).status_code, 404)


class FailedReviewWaitsForLee(Prototypes):
    def failed_review(self):
        order_id, _ = self.built(BROKEN)
        out = pr.review(order_id, browser=stand_in_browser(lambda req: "£255" not in req))
        self.assertEqual(out["result"], "failed")
        return order_id

    def test_failed_review_reports_passed_and_failed_and_starts_nothing(self):
        order_id = self.failed_review()
        s = self.status(order_id)
        self.assertEqual(s["state"], "review_failed")
        self.assertTrue(s["decision_required"])
        self.assertTrue(any("£260" in p for p in s["findings"]["passed"]))
        self.assertTrue(all("£255" in f for f in s["findings"]["failed"]))
        self.assertIsNone(self.claim())
        self.assertIn("prototype_path", s)       # Lee can still open what was built

    def test_lee_requests_a_fix_once_and_workbench_gets_the_findings(self):
        order_id = self.failed_review()
        r = self.client.post(f"/ayven/prototypes/{order_id}/decision", headers=self.owner, json={"decision": "fix"})
        self.assertEqual(r.status_code, 200, r.text)
        again = self.client.post(f"/ayven/prototypes/{order_id}/decision", headers=self.owner,
                                 json={"decision": "fix"}).json()
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["fix_order_id"], r.json()["fix_order_id"])
        self.assertEqual(self.client.post(f"/ayven/prototypes/{order_id}/decision", headers=self.owner,
                                          json={"decision": "no_fix"}).status_code, 409)
        job = self.claim()
        self.assertEqual(job["order_id"], r.json()["fix_order_id"])
        self.assertEqual(job["request_key"], "job-alba-doors-test:plan-rev-1:fix-1")
        self.assertIn("£255", job["review_findings"])
        self.assertIsNone(self.claim())          # exactly one fix build
        self.assertEqual(self.status(order_id)["state"], "fix_requested")

    def test_lee_declines_a_fix(self):
        order_id = self.failed_review()
        r = self.client.post(f"/ayven/prototypes/{order_id}/decision", headers=self.owner, json={"decision": "no_fix"})
        self.assertEqual(r.json()["state"], "closed")
        self.assertIsNone(self.claim())

    def test_read_only_cannot_decide_and_passed_review_has_nothing_to_decide(self):
        order_id = self.failed_review()
        self.assertEqual(self.client.post(f"/ayven/prototypes/{order_id}/decision",
                                          headers={"Authorization": "Bearer " + READ},
                                          json={"decision": "fix"}).status_code, 401)
        ok_id = self.enqueue(plan=dict(copy.deepcopy(PLAN), job_id="job-other")).json()["order_id"]
        job = self.claim()
        self.post(job, signed_result(job, GOOD))
        pr.review(ok_id, browser=stand_in_browser(lambda _r: True))
        self.assertEqual(self.client.post(f"/ayven/prototypes/{ok_id}/decision", headers=self.owner,
                                          json={"decision": "fix"}).status_code, 409)


class Teams(Prototypes):
    def test_competing_jobs_get_a_recommendation_and_wait_for_lee(self):
        a = self.client.post("/ayven/teams/assign", headers=self.owner, json={
            "job_id": "job-kitchen", "role": "review", "team": "labs",
            "inputs": {"title": "Kitchen quote tool", "deadline": "2026-11-20", "approved": True}}).json()
        b = self.client.post("/ayven/teams/assign", headers=self.owner, json={
            "job_id": "job-alba", "role": "review", "team": "labs",
            "inputs": {"title": "Alba door calculator", "deadline": "2026-11-12", "approved": True}}).json()
        self.assertEqual((a["status"], b["status"]), ("active", "waiting_priority"))
        self.assertEqual(a["agent_id"], "labs-validator")
        pending = self.client.get("/ayven/teams", headers=self.owner).json()["pending_decisions"]
        self.assertEqual(len(pending), 1)
        d = pending[0]
        self.assertEqual(d["recommended_job"], "job-alba")
        self.assertIn("due 2026-11-12", d["reasons"][0])
        self.assertIn("waits for Lee", d["reasons"][-1])
        # Campus did not switch the team on its own.
        self.assertEqual(self.team_status("labs"), {"job-kitchen": "active", "job-alba": "waiting_priority"})
        r = self.client.post(f"/ayven/priority/{d['id']}/decide", headers=self.owner, json={"job_id": "job-kitchen"})
        self.assertEqual(r.json()["recommended_job"], "job-alba")       # Lee may overrule; both are recorded
        self.assertEqual(self.team_status("labs"), {"job-kitchen": "active", "job-alba": "queued"})
        nxt = self.client.post("/ayven/teams/labs/release", headers=self.owner,
                               json={"job_id": "job-kitchen", "role": "review"}).json()
        self.assertEqual(nxt["next_active"], "job-alba")
        self.assertEqual(self.client.post(f"/ayven/priority/{d['id']}/decide", headers=self.owner,
                                          json={"job_id": "job-alba"}).status_code, 409)

    def team_status(self, team):
        t = next(x for x in self.client.get("/ayven/teams", headers=self.owner).json()["teams"] if x["team"] == team)
        return {a["job_id"]: a["status"] for a in t["assignments"]}

    def test_review_waits_while_the_review_team_is_busy_with_another_job(self):
        self.client.post("/ayven/teams/assign", headers=self.owner, json={
            "job_id": "job-kitchen", "role": "review", "team": "labs", "inputs": {"deadline": "2026-11-30"}})
        order_id, _ = self.built()
        out = pr.review(order_id, browser=stand_in_browser(lambda _r: True))
        self.assertEqual(out["waiting_for"], "priority_decision")
        self.assertEqual(self.status(order_id)["state"], "reviewing")
        d = self.client.get("/ayven/priority", headers=self.owner).json()["decisions"][0]
        self.assertEqual(d["recommended_job"], "job-alba-doors-test")    # earlier deadline (2026-11-12)
        with patch.object(pr, "start_review", lambda oid: pr.review(oid, browser=stand_in_browser(lambda _r: True))):
            self.client.post(f"/ayven/priority/{d['id']}/decide", headers=self.owner,
                             json={"job_id": "job-alba-doors-test"})
        self.assertEqual(self.status(order_id)["state"], "review_passed")


@unittest.skipUnless(review_python(), "Playwright not installed for AYVEN_REVIEW_PYTHON; real browser review not run")
class RealBrowserReview(Prototypes):
    env_extra = dict(Prototypes.env_extra, AYVEN_ALLOW_BROWSER_BUILD="1")

    def test_alba_prototype_passes_campus_review_including_the_held_out_check(self):
        order_id, _ = self.built(GOOD)
        out = pr.review(order_id)
        self.assertEqual(out["result"], "passed", out)
        self.assertEqual(len(out["passed"]), 8)          # 3 approved + 1 held-out journeys x 2 widths
        self.assertTrue(any("£260" in p and "1280" in p for p in out["passed"]))
        self.assertTrue(any("£260" in p and "390" in p for p in out["passed"]))
        self.assertEqual(self.status(order_id)["state"], "review_passed")

    def test_broken_prototype_passes_workbench_journeys_but_fails_campus_review(self):
        order_id, _ = self.built(BROKEN)
        out = pr.review(order_id)
        self.assertEqual(out["result"], "failed")
        self.assertTrue(any("£260" in p for p in out["passed"]))
        self.assertTrue(any("£255" in f for f in out["failed"]))
        s = self.status(order_id)
        self.assertEqual((s["state"], s["decision_required"]), ("review_failed", True))
        self.assertIsNone(self.claim())


if __name__ == "__main__":
    unittest.main()


class BriefApprovalLifecycle(Prototypes):
    def test_revision_approval_queues_once_and_enters_existing_review(self):
        url = "/ayven/prototype-plans"
        brief = {"brief": "Build the Alba calculator", "request_key": "alba-lifecycle"}
        with patch("apps.api.app.llm.complete", return_value=(json.dumps(PLAN), 100)):
            created = self.client.post(url, headers=self.owner, json=brief)
            self.assertEqual(created.status_code, 200, created.text)
            first = created.json()
            self.assertEqual(self.client.post(url, headers=self.owner, json=brief).json(), first)
            self.assertIsNone(self.claim())
            self.assertEqual(self.enqueue(plan=first["plan"]).status_code, 409)
            decision = f"{url}/{first['job_id']}/decision"
            revised = self.client.post(decision, headers=self.owner, json={
                "action": "revise", "digest": first["digest"], "notes": "Confirm the two prices",
            })
            self.assertEqual(revised.status_code, 200, revised.text)
        plan = revised.json()
        self.assertEqual(plan["job_id"], first["job_id"])
        self.assertEqual(plan["plan"]["revision"], 2)
        self.assertEqual(self.client.post(decision, headers=self.owner, json={
            "action": "approve", "digest": first["digest"],
        }).status_code, 409)
        self.assertIsNone(self.claim())
        body = {"action": "approve", "digest": plan["digest"]}
        queued = self.client.post(decision, headers=self.owner, json=body)
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(self.client.post(decision, headers=self.owner, json=body).json(), queued.json())
        job = self.claim()
        self.assertEqual(job["order_id"], queued.json()["order_id"])
        self.assertIsNone(self.claim())
        result = self.post(job, signed_result(job, GOOD))
        self.assertEqual(result.status_code, 200, result.text)
