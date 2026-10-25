"""Ayven OS service layer: the shared action contract, real persistence in the platform's own tables, owner
authorisation, and a durable audit trail. Runs against the real Campus app over HTTP (TestClient)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

OWNER = "owner-token-" + "o" * 32
READ = "read-token-" + "r" * 32


class OSBase:
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "AYVEN_DB": str(Path(self.tmp.name) / "campus.sqlite"), "AYVEN_ENV": "local",
            "AYVEN_API_TOKEN": OWNER, "AYVEN_READ_TOKEN": READ, "AYVEN_LLM_STUB": "1"})
        self.env.start()
        from apps.api.app.db import connect, init_db, seed, reset_connection_state
        reset_connection_state()
        c = connect()
        init_db(c)
        seed(c)             # the platform's real departments and agents
        c.close()
        self._reset = reset_connection_state
        self.client = TestClient(__import__("apps.api.app.main", fromlist=["app"]).app)
        self.owner = {"Authorization": "Bearer " + OWNER}
        self.read = {"Authorization": "Bearer " + READ}

    def teardown_method(self):
        self.env.stop()
        self._reset()
        self.tmp.cleanup()

    def invoke(self, action, body=None, headers=None):
        return self.client.post("/os/actions/" + action, headers=headers or self.owner, json=body or {})


class TestContractAndAuth(OSBase):
    def test_the_manifest_is_the_shared_contract(self):
        r = self.client.get("/os/actions", headers=self.owner)
        assert r.status_code == 200
        body = r.json()
        assert body["schema"] == "ayven.os/v1"
        ids = {a["id"] for a in body["actions"]}
        assert {"agent.list", "agent.create", "agent.retire", "project.list", "execution.list"} <= ids
        create = next(a for a in body["actions"] if a["id"] == "agent.create")
        assert create["mutates"] is True and create["auth"] == "owner" and create["method"] == "POST"
        assert "name" in create["input_schema"] and create["example_input"]["department_id"] == "research"
        # A read-only token may read the contract.
        assert self.client.get("/os/actions", headers=self.read).status_code == 200

    def test_unauthenticated_and_unknown_are_refused(self):
        assert self.client.get("/os/actions").status_code in (401, 303)
        r = self.invoke("does.not.exist")
        assert r.status_code == 404

    def test_a_read_only_token_reads_the_contract_but_cannot_invoke(self):
        # Invocation is POST; the Campus access boundary only lets a read-only token GET, so it can read the
        # manifest and the audit trail but cannot invoke any action (not even a read one). Stronger than the
        # per-action read-only guard, which stays as defence in depth.
        assert self.client.get("/os/actions", headers=self.read).status_code == 200
        assert self.client.get("/os/audit", headers=self.read).status_code == 200
        assert self.invoke("agent.list", headers=self.read).status_code == 401
        assert self.invoke("agent.create", {"name": "X", "role": "Y", "department_id": "research"},
                           headers=self.read).status_code == 401


class TestAgentVerticalSlice(OSBase):
    def test_create_persists_in_the_platform_and_appears_in_state(self):
        r = self.invoke("agent.create", {"name": "Grace Researcher", "role": "Analyst", "department_id": "research"})
        assert r.status_code == 200, r.text
        agent = r.json()["result"]["agent"]
        assert agent["id"].startswith("ag-") and agent["status"] == "idle" and agent["managed_by"] == "ayven-os"
        # It is the real agents table: it shows in agent.list, agent.get and the platform's /state.
        listed = self.invoke("agent.list", {"department_id": "research"}).json()["result"]["agents"]
        assert any(a["id"] == agent["id"] for a in listed)
        got = self.invoke("agent.get", {"id": agent["id"]}).json()["result"]["agent"]
        assert got["name"] == "Grace Researcher"
        state_agents = self.client.get("/state", headers=self.owner).json()["agents"]
        assert any(a["id"] == agent["id"] for a in state_agents)

    def test_create_validates_name_and_department(self):
        assert self.invoke("agent.create", {"name": "", "role": "A", "department_id": "research"}).status_code == 400
        bad = self.invoke("agent.create", {"name": "ok", "role": "A", "department_id": "nope"})
        assert bad.status_code == 400 and "department" in bad.json()["detail"]
        inj = self.invoke("agent.create", {"name": "a‮evil", "role": "A", "department_id": "research"})
        assert inj.status_code == 400

    def test_retire_is_guarded_and_reversible_state(self):
        agent = self.invoke("agent.create", {"name": "Temp", "role": "A", "department_id": "labs"}).json()["result"]["agent"]
        r = self.invoke("agent.retire", {"id": agent["id"]})
        assert r.status_code == 200 and r.json()["result"]["agent"]["status"] == "retired"
        # A busy agent cannot be retired.
        from apps.api.app.db import connect
        c = connect()
        c.execute("UPDATE agents SET status='working', current_task_id='t1' WHERE id=?", (agent["id"],))
        c.commit(); c.close()
        busy = self.invoke("agent.retire", {"id": agent["id"]})
        assert busy.status_code == 409
        assert self.invoke("agent.retire", {"id": "ag-nope"}).status_code == 404

    def test_seeded_agents_are_untouched_by_the_migration(self):
        # The provenance columns are nullable; a seeded agent has no managed_by and still lists cleanly.
        agents = self.invoke("agent.list").json()["result"]["agents"]
        seeded = [a for a in agents if a.get("managed_by") in (None, "")]
        assert seeded, "seeded agents should still be present"


class TestProjectsAndExecutions(OSBase):
    def test_project_list_and_get_read_real_rows(self):
        from apps.api.app.db import connect
        c = connect()
        c.execute("INSERT INTO projects(id,title,objective,status,created_at) VALUES"
                  "('proj-1','Takeaway','Build pricing','running','2026-10-25T10:00:00Z')")
        c.execute("INSERT INTO tasks(id,project_id,title,brief,status,created_at) VALUES"
                  "('t-1','proj-1','Price','do it','queued','2026-10-25T10:00:00Z')")
        c.commit(); c.close()
        projects = self.invoke("project.list", {"limit": 10}).json()["result"]["projects"]
        assert any(p["id"] == "proj-1" for p in projects)
        got = self.invoke("project.get", {"id": "proj-1"}).json()["result"]
        assert got["project"]["title"] == "Takeaway" and got["tasks"][0]["id"] == "t-1"
        assert self.invoke("project.get", {"id": "nope"}).status_code == 404

    def test_execution_list_bridges_the_workbench_lane(self):
        r = self.invoke("execution.list", {"limit": 5})
        assert r.status_code == 200 and "executions" in r.json()["result"]


class TestAudit(OSBase):
    def test_every_invocation_is_audited_with_actor_and_outcome(self):
        self.invoke("agent.create", {"name": "Audited", "role": "A", "department_id": "research"},
                    headers={**self.owner, "X-Ayven-Owner": "lee@example.invalid"})
        self.invoke("agent.get", {"id": "ag-missing"})                     # refused (404)
        entries = self.client.get("/os/audit", headers=self.owner).json()["entries"]
        by_action = {}
        for e in entries:
            by_action.setdefault(e["action"], []).append(e)
        created = next(e for e in by_action["agent.create"] if e["outcome"] == "ok")
        assert created["actor"] == "lee@example.invalid" and created["mutates"] == 1
        assert len(created["input_sha256"]) == 64 and "name=Audited" in created["input_summary"]
        assert any(e["outcome"] == "refused" for e in by_action["agent.get"])
        # Filterable, and never records a raw value beyond a short summary.
        filtered = self.client.get("/os/audit?action=agent.create", headers=self.owner).json()["entries"]
        assert filtered and all(e["action"] == "agent.create" for e in filtered)
