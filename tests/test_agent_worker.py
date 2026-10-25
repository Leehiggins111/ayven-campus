"""Bounded agent-task execution: a registered agent becomes a real worker for one task kind.
Validation, genuine per-role permission checks, honest execution (no simulated success for a real
backend failure), durable state, linked evidence, cancellation, retry, and audit — over the real
Campus app (TestClient)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

OWNER = "owner-token-" + "o" * 32


class WorkerBase:
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "AYVEN_DB": str(Path(self.tmp.name) / "campus.sqlite"), "AYVEN_ENV": "local",
            "AYVEN_API_TOKEN": OWNER, "AYVEN_LLM_STUB": "1"})
        self.env.start()
        from apps.api.app.db import connect, init_db, seed, reset_connection_state
        reset_connection_state()
        c = connect(); init_db(c); seed(c); c.close()
        self._reset = reset_connection_state
        self.client = TestClient(__import__("apps.api.app.main", fromlist=["app"]).app)
        self.owner = {"Authorization": "Bearer " + OWNER, "X-Ayven-Owner": "lee@ayven.local"}

    def teardown_method(self):
        self.env.stop()
        self._reset()
        self.tmp.cleanup()

    def invoke(self, action, body=None):
        return self.client.post("/os/actions/" + action, headers=self.owner, json=body or {})


class TestAssignmentAndValidation(WorkerBase):
    def test_a_permitted_agent_runs_a_task_and_records_evidence(self):
        # research-e1 has READ_WEB, so it may perform a research_brief. In stub mode the result is
        # labelled SIMULATED (honest), but the full lifecycle, persistence and evidence are real.
        r = self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief",
                                          "objective": "Summarise the UK independent coffee shop market."})
        assert r.status_code == 200, r.text
        task = r.json()["result"]["task"]
        assert task["status"] == "completed"
        assert task["agent_id"] == "research-e1" and task["owner"] == "lee@ayven.local"
        assert task["execution_mode"] == "SIMULATED"            # no real local model in this env
        assert task["result_text"] and len(task["result_sha256"]) == 64
        assert task["evidence"]["capability"] == "READ_WEB" and task["evidence"]["tool"] == "model_gateway"
        # The agent is released back to idle after the work.
        agent = self.invoke("agent.get", {"id": "research-e1"}).json() if False else \
            self.client.post("/os/actions/agent.get", headers=self.owner, json={"id": "research-e1"}).json()["result"]["agent"]
        assert agent["status"] == "idle" and not agent["current_task_id"]

    def test_an_unpermitted_agent_is_rejected_and_audited(self):
        # 'compliance' has no capabilities in ROLE_CAPS, so it cannot be given a research_brief.
        r = self.invoke("agent.assign", {"agent_id": "compliance", "kind": "research_brief",
                                         "objective": "anything"})
        assert r.status_code == 403
        assert "not permitted" in r.json()["detail"]
        audit = self.client.get("/os/audit?action=agent.assign", headers=self.owner).json()["entries"]
        assert any(e["outcome"] == "forbidden" for e in audit)

    def test_unknown_agent_kind_and_empty_objective(self):
        assert self.invoke("agent.assign", {"agent_id": "ag-nope", "kind": "research_brief", "objective": "x"}).status_code == 404
        assert self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "teleport", "objective": "x"}).status_code == 400
        assert self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief", "objective": ""}).status_code == 400

    def test_a_busy_agent_cannot_be_assigned(self):
        from apps.api.app.db import connect
        c = connect(); c.execute("UPDATE agents SET status='working', current_task_id='t1' WHERE id='research-e1'"); c.commit(); c.close()
        r = self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief", "objective": "x"})
        assert r.status_code == 409

    def test_permitted_kinds_distinguish_capable_from_incapable_agents(self):
        cap = self.client.post("/os/actions/agent.get", headers=self.owner, json={"id": "research-e1"}).json()["result"]["agent"]
        assert cap["permitted_task_kinds"] == ["research_brief"] and cap["active"] is False
        incap = self.client.post("/os/actions/agent.get", headers=self.owner, json={"id": "compliance"}).json()["result"]["agent"]
        assert incap["permitted_task_kinds"] == []


def _unavailable_backend():
    # A real local backend is configured but the gateway raises (connection refused / down). Patches
    # the model call directly so the test is fast and deterministic, not dependent on socket timing.
    def boom(*a, **k):
        raise ConnectionError("refused")
    return mock.patch.dict(os.environ, {"AYVEN_LOCAL_LLM_BASE_URL": "http://local", "AYVEN_LLM_STUB": "0"}), \
        mock.patch("apps.api.app.models._openai_compat", boom)


class TestHonestFailure(WorkerBase):
    def test_a_real_backend_failure_is_a_failed_task_not_a_false_success(self):
        # A real backend is configured with the stub OFF, but it is down. The task must fail visibly;
        # no fixture answer may be substituted.
        env, patch = _unavailable_backend()
        with env, patch:
            r = self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief",
                                             "objective": "Summarise something."})
        assert r.status_code == 200
        task = r.json()["result"]["task"]
        assert task["status"] == "failed"
        assert task["result_text"] in (None, "") and task["execution_mode"] is None
        assert "unavailable" in (task["error"] or "")
        # The agent is released, not left stuck as working.
        agent = self.client.post("/os/actions/agent.get", headers=self.owner, json={"id": "research-e1"}).json()["result"]["agent"]
        assert agent["status"] == "idle"

    def test_retry_of_a_failed_task_can_succeed(self):
        env, patch = _unavailable_backend()
        with env, patch:
            failed = self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief",
                                                  "objective": "Summarise something."}).json()["result"]["task"]
        assert failed["status"] == "failed"
        # Back in stub mode, a retry runs a fresh attempt linked to the original.
        retried = self.invoke("task.retry", {"id": failed["id"]}).json()["result"]["task"]
        assert retried["status"] == "completed" and retried["parent_task_id"] == failed["id"] and retried["attempt"] == 2


class TestCancellationAndInspection(WorkerBase):
    def test_a_queued_task_can_be_cancelled(self):
        queued = self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief",
                                              "objective": "x", "start": False}).json()["result"]["task"]
        assert queued["status"] == "queued"
        cancelled = self.invoke("task.cancel", {"id": queued["id"]}).json()["result"]["task"]
        assert cancelled["status"] == "cancelled"
        assert self.invoke("task.cancel", {"id": queued["id"]}).status_code == 409   # already terminal

    def test_tasks_are_inspectable_by_agent_and_status(self):
        self.invoke("agent.assign", {"agent_id": "research-e1", "kind": "research_brief", "objective": "a"})
        self.invoke("agent.assign", {"agent_id": "research-e2", "kind": "research_brief", "objective": "b"})
        only_e1 = self.invoke("task.list", {"agent_id": "research-e1"}).json()["result"]["tasks"]
        assert only_e1 and all(t["agent_id"] == "research-e1" for t in only_e1)
        completed = self.invoke("task.list", {"status": "completed"}).json()["result"]["tasks"]
        assert len(completed) >= 2
        one = self.invoke("task.get", {"id": only_e1[0]["id"]}).json()["result"]["task"]
        assert one["evidence"] is not None
        assert self.invoke("task.get", {"id": "tk-nope"}).status_code == 404


class TestContractExposesWorkforce(WorkerBase):
    def test_the_new_actions_are_in_the_shared_manifest(self):
        ids = {a["id"] for a in self.client.get("/os/actions", headers=self.owner).json()["actions"]}
        assert {"agent.assign", "task.get", "task.list", "task.cancel", "task.retry"} <= ids
        assign = next(a for a in self.client.get("/os/actions", headers=self.owner).json()["actions"] if a["id"] == "agent.assign")
        assert assign["mutates"] is True and assign["category"] == "workforce"
