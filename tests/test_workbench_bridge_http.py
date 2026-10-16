"""Experimental Campus bridge HTTP contract tests; requires FastAPI TestClient."""
import hashlib
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

class BridgeHTTP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "AYVEN_DB": str(Path(self.tmp.name) / "campus.sqlite"),
            "AYVEN_ENV":"local", "AYVEN_API_TOKEN":OWNER,
            "AYVEN_WORKER_TOKEN":WORKER, "AYVEN_LLM_STUB":"1"
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

    def enqueue(self,key="key-one",objective="Create hello.py"):
        return self.client.post("/milo/executions",headers={**self.owner,"Idempotency-Key":key},
            json={"objective":objective,"project_alias":"cloud-demo",
                  "acceptance_criteria":["file exists"],"job_cap_gbp":0})

    def test_worker_auth_and_claim_once(self):
        r=self.enqueue()
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.post("/internal/workbench/claim",json={"worker_id":"a"}).status_code,401)
        a=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"a"})
        self.assertEqual(a.status_code,200,a.text)
        self.assertIsNotNone(a.json()["job"])
        b=self.client.post("/internal/workbench/claim",headers=self.worker,json={"worker_id":"b"})
        self.assertIsNone(b.json()["job"])

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
              "evidence":["3 tests passed"],"defects":[],"artifacts":[
                  {"name":"hello.py","content":"print('hello')\n",
                   "sha256":hashlib.sha256(b"print('hello')\n").hexdigest()}]}
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
                  "execution_mode":"SIMULATED","evidence":["fixture"],"artifacts":[]}).status_code,200)
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
                "defects":[],"artifacts":[{"name":"fixture.py","content":content,
                "sha256":hashlib.sha256(content.encode()).hexdigest()}]}
        reply=self.client.post("/internal/workbench/"+job["order_id"]+"/result",headers=self.worker,json=result)
        self.assertEqual(reply.status_code,200,reply.text)
        reset_connection_state()
        state=self.client.get("/milo/jobs/"+project_id,headers=self.owner)
        self.assertEqual(state.status_code,200,state.text)
        self.assertEqual(state.json()["status"],"waiting")
        self.assertEqual(state.json()["mode"],"SIMULATED")
        self.assertEqual(self.client.post("/milo/executions/"+job["order_id"]+"/decision",
            headers=self.owner,json={"decision":"accept"}).status_code,409)
        self.assertEqual(self.client.get("/milo/executions/"+job["order_id"]+
            "/artifacts/fixture.py",headers=self.owner).status_code,409)
        rejected=self.client.post("/milo/executions/"+job["order_id"]+"/decision",
            headers=self.owner,json={"decision":"reject","reason":"Simulation is not a deliverable"})
        self.assertEqual(rejected.status_code,200,rejected.text)
        self.assertEqual(self.client.get("/milo/jobs/"+project_id,headers=self.owner).json()["status"],"rejected")

if __name__=="__main__":
    unittest.main()
