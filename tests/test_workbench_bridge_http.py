"""Experimental Campus bridge HTTP contract tests; requires FastAPI TestClient."""
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
from workbench_signed import KEY as ATTESTATION_KEY, signed_evidence

OWNER = "o" * 40
WORKER = "w" * 40


DEFAULT_ALLOWED = ["fixture.py", "hello.py"]


def evidence_for(patch, order_id="key-one", allowed=DEFAULT_ALLOWED):
    """The Workbench's signed verification of this exact patch for this order (see workbench_signed)."""
    return signed_evidence(patch, order_id, allowed)

class BridgeHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "AYVEN_DB": str(Path(self.tmp.name) / "campus.sqlite"),
            "AYVEN_ENV":"local", "AYVEN_API_TOKEN":OWNER,
            "AYVEN_WORKER_TOKEN":WORKER, "AYVEN_LLM_STUB":"1", "AYVEN_ATTESTATION_KEY":ATTESTATION_KEY
        })
        self.env.start()
        reset_connection_state()
        c=connect()
        init_db(c)
        c.close()
        self.client=TestClient(app)
        self.owner={"Authorization":"Bearer "+OWNER}
        self.worker={"Authorization":"Bearer "+WORKER}

    def tearDown(self):
        self.env.stop()
        reset_connection_state()
        self.tmp.cleanup()

    def enqueue(self,key="key-one",objective="Create hello.py",allowed=("hello.py","fixture.py")):
        return self.client.post("/milo/executions",headers={**self.owner,"Idempotency-Key":key},
            json={"objective":objective,"project_alias":"cloud-demo",
                  "acceptance_criteria":["file exists"],"job_cap_gbp":0,"allowed_files":list(allowed)})

    @staticmethod
    def patch_artifact(*paths):
        """C3: a PASS must carry a changes.diff that Campus can check against the allow-list."""
        diff="".join("diff --git a/%s b/%s\nnew file mode 100644\n--- /dev/null\n+++ b/%s\n@@ -0,0 +1 @@\n+x\n"%(p,p,p) for p in paths)
        return {"name":"changes.diff","content":diff,"sha256":hashlib.sha256(diff.encode()).hexdigest()}

    def test_worker_auth_and_claim_once(self):
        r=self.enqueue()
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.post("/internal/workbench/claim",json={"worker_id":"a"}).status_code,401)
        a=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"a"})
        self.assertEqual(a.status_code,200,a.text)
        self.assertIsNotNone(a.json()["job"])
        b=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"b"})
        self.assertIsNone(b.json()["job"])

    def test_readonly_preview_is_separate_from_unaccepted_deliverable(self):
        self.assertEqual(self.enqueue(key="preview-1").status_code, 200)
        queued = self.client.get("/milo/executions/preview-1/preview/changes.diff",
                                 headers=self.owner)
        self.assertEqual(queued.status_code, 409)
        claim = self.client.post("/internal/workbench/claim", headers=self.worker,
                                 json={"worker_id": "a"}).json()["job"]
        patch = self.patch_artifact("hello.py")
        body = {"worker_id": "a", "fence": claim["fence"], "verdict": "PASS",
                "execution_mode": "REAL", "evidence": ["unittest passed"],
                "defects": [], "artifacts": [patch, *evidence_for(patch, "preview-1")]}
        result = self.client.post("/internal/workbench/preview-1/result",
                                  headers=self.worker, json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["state"], "awaiting_approval")
        preview = self.client.get("/milo/executions/preview-1/preview/changes.diff",
                                  headers=self.owner)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.content.decode(), patch["content"])
        self.assertEqual(preview.headers["X-Ayven-Verification"], "VERIFIED")
        self.assertEqual(preview.headers["X-Ayven-Outcome"], "AWAITING_APPROVAL")
        self.assertEqual(self.client.get("/milo/executions/preview-1/preview/hello.py",
                                         headers=self.owner).status_code, 404)
        self.assertEqual(self.client.get("/milo/executions/preview-1/artifacts/changes.diff",
                                         headers=self.owner).status_code, 409)

    def test_owner_execution_list_survives_state_changes(self):
        first = self.enqueue(key="list-1", objective="Fix pricing", allowed=("pricing.py",))
        self.assertEqual(first.status_code, 200, first.text)
        other = self.enqueue(key="list-2", objective="Fix stock", allowed=("stock.py",))
        self.assertEqual(other.status_code, 200, other.text)
        unauthenticated = self.client.get("/milo/executions")
        self.assertIn(unauthenticated.status_code, (401, 403))
        listing = self.client.get("/milo/executions?limit=10", headers=self.owner)
        self.assertEqual(listing.status_code, 200, listing.text)
        orders = {x["order_id"]: x for x in listing.json()["executions"]}
        self.assertEqual(set(orders), {"list-1", "list-2"})
        self.assertEqual(orders["list-1"]["allowed_files"], ["pricing.py"])
        self.assertEqual(orders["list-1"]["state"], "queued")
        self.assertFalse(orders["list-1"]["tests_verified"])
        claim = self.client.post("/internal/workbench/claim",
                                 headers=self.worker, json={"worker_id": "a"}).json()["job"]
        updated = self.client.get("/milo/executions", headers=self.owner).json()["executions"]
        running = next(x for x in updated if x["order_id"] == claim["order_id"])
        self.assertEqual(running["state"], "running")
        self.assertEqual(running["attempts"], 1)
        self.assertEqual(self.client.get("/milo/executions?limit=0",
                                         headers=self.owner).status_code, 400)
        self.assertEqual(self.client.get("/milo/executions?limit=101",
                                         headers=self.owner).status_code, 400)

    def test_idempotency_conflict(self):
        self.assertEqual(self.enqueue().status_code,200)
        self.assertTrue(self.enqueue().json()["duplicate"])
        self.assertEqual(self.enqueue(objective="Different job").status_code,409)

    def test_fenced_result_and_owner_gate(self):
        self.assertEqual(self.enqueue().status_code,200)
        job=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"a"}).json()["job"]
        order=job["order_id"]
        path="/internal/workbench/"+order+"/result"
        body={"worker_id":"a","fence":job["fence"],"verdict":"PASS","execution_mode":"REAL",
              "evidence":["3 tests passed"],"defects":[],"artifacts":[self.patch_artifact("hello.py"),
                  {"name":"hello.py","content":"print('hello')\n",
                   "sha256":hashlib.sha256(b"print('hello')\n").hexdigest()},
                  *evidence_for(self.patch_artifact("hello.py"))]}
        self.assertEqual(self.client.post(path,headers=self.worker,json={**body,"fence":999}).status_code,409)
        self.assertEqual(self.client.post(path,headers=self.worker,json=body).status_code,200)
        download="/milo/executions/"+order+"/artifacts/hello.py"
        self.assertEqual(self.client.get(download,headers=self.owner).status_code,409)
        self.assertEqual(self.client.post("/milo/executions/"+order+"/decision",
            headers=self.owner,json={"decision":"accept"}).status_code,200)
        self.assertEqual(self.client.get(download,headers=self.owner).content,b"print('hello')\n")
        self.assertEqual(self.client.post(path,headers=self.worker,json=body).status_code,200)
        self.assertTrue(self.client.post(path,headers=self.worker,json=body).json()["duplicate"])
        self.assertEqual(self.client.post(path,headers=self.worker,json={**body,"evidence":["different"]}).status_code,409)

    def test_hash_mismatch(self):
        self.assertEqual(self.enqueue().status_code,200)
        job=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"a"}).json()["job"]
        r=self.client.post("/internal/workbench/"+job["order_id"]+"/result",headers=self.worker,
            json={"worker_id":"a","fence":job["fence"],"verdict":"PASS",
                  "execution_mode":"REAL","evidence":["ok"],"artifacts":[
                  {"name":"x.txt","content":"abc","sha256":"0"*64}]})
        self.assertEqual(r.status_code,400)

    def test_simulated_cannot_be_accepted(self):
        self.assertEqual(self.enqueue().status_code,200)
        job=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"a"}).json()["job"]
        self.assertEqual(self.client.post("/internal/workbench/"+job["order_id"]+"/result",headers=self.worker,
            json={"worker_id":"a","fence":job["fence"],"verdict":"PASS",
                  "execution_mode":"SIMULATED","evidence":["fixture"],
                  "artifacts":[self.patch_artifact("hello.py"),
                               *evidence_for(self.patch_artifact("hello.py"))]}).status_code,200)
        self.assertEqual(self.client.post("/milo/executions/"+job["order_id"]+"/decision",
            headers=self.owner,json={"decision":"accept"}).status_code,409)

    def test_duplicate_after_claim(self):
        self.assertEqual(self.enqueue().status_code, 200)
        self.client.post("/internal/workbench/claim", headers=self.worker, json={"worker_id":"a"})
        again = self.enqueue()
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(again.json()["state"], "running")

    def test_workbench_state_projects_to_milo(self):
        submission = self.enqueue()
        self.assertEqual(submission.status_code, 200, submission.text)
        project_id = submission.json()["project_id"]
        status = self.client.get("/milo/jobs/"+project_id, headers=self.owner)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "queued")
        self.assertEqual(status.json()["engine"], "workbench")
        self.assertEqual(status.json()["mode"], "PENDING_REAL")
        claimed = self.client.post("/internal/workbench/claim", headers=self.worker, json={"worker_id":"a"}).json()["job"]
        status = self.client.get("/milo/jobs/"+project_id, headers=self.owner)
        self.assertEqual(status.json()["status"], "running")
        self.assertEqual(status.json()["execution"]["order_id"], claimed["order_id"])

    def test_simulated_journey_survives_connection_restart(self):
        submission=self.enqueue(key="restart-journey")
        self.assertEqual(submission.status_code,200,submission.text)
        project_id=submission.json()["project_id"]
        job=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"qa-worker"}).json()["job"]
        self.assertEqual(job["project_id"],project_id)
        content="print('verified fixture')\\n"
        result={"worker_id":"qa-worker","fence":job["fence"],"verdict":"PASS",
                "execution_mode":"SIMULATED","evidence":["deterministic fixture completed"],
                "defects":[],"artifacts":[self.patch_artifact("fixture.py"),{"name":"fixture.py","content":content,
                "sha256":hashlib.sha256(content.encode()).hexdigest()},*evidence_for(self.patch_artifact("fixture.py"), "restart-journey")]}
        reply=self.client.post("/internal/workbench/"+job["order_id"]+"/result",headers=self.worker,json=result)
        self.assertEqual(reply.status_code,200,reply.text)
        reset_connection_state()
        state=self.client.get("/milo/jobs/"+project_id,headers=self.owner)
        self.assertEqual(state.status_code,200,state.text)
        self.assertEqual(state.json()["status"],"simulated")
        self.assertFalse(state.json()["waiting_for_lee"])
        self.assertIsNone(state.json()["approval"])
        self.assertEqual(state.json()["mode"],"SIMULATED")
        self.assertEqual(self.client.post("/milo/executions/"+job["order_id"]+"/decision",
            headers=self.owner,json={"decision":"accept"}).status_code,409)
        self.assertEqual(self.client.get("/milo/executions/"+job["order_id"]+
            "/artifacts/fixture.py",headers=self.owner).status_code,409)
        rejected=self.client.post("/milo/executions/"+job["order_id"]+"/decision",
            headers=self.owner,json={"decision":"reject","reason":"Simulation is not a deliverable"})
        self.assertEqual(rejected.status_code,409,rejected.text)
        self.assertEqual(self.client.get("/milo/jobs/"+project_id,headers=self.owner).json()["status"],"simulated")

if __name__=="__main__":
    unittest.main()
