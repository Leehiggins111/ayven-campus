from fastapi.testclient import TestClient

from app.main import app


def test_health():
    c = TestClient(app)
    assert c.get("/health").json()["ok"] is True


def test_campus_page_served():
    c = TestClient(app)
    r = c.get("/campus")
    assert r.status_code == 200
    assert "Ayven Campus" in r.text
    assert "/r3f/campus-app.jsx" in r.text
    jsx = c.get("/r3f/campus-app.jsx")
    assert jsx.status_code == 200
    assert "Ask Milo" in jsx.text
    assert "WorkCrate" in jsx.text


def test_state_seeded():
    c = TestClient(app)
    s = c.get("/state").json()
    assert len(s["departments"]) >= 5
    assert any(a["id"] == "milo" for a in s["agents"])


def test_a_repeated_submission_returns_the_same_project():
    import uuid

    from app.db import connect

    c = TestClient(app)
    objective = "Prepare a UK business briefing for a coffee machine rental company. Cover the service. duplicate-check."
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, "duplicate-check", objective, "running", "2026-10-11T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    second = c.post("/projects", json={"objective": "  " + objective + "  "}).json()
    assert second["project_id"] == project_id
    assert second["duplicate"] is True
    conn = connect()
    conn.execute("UPDATE projects SET status=? WHERE id=?", ("unresolved", project_id))
    conn.commit()
    conn.close()
    third = c.post("/projects", json={"objective": objective}).json()
    assert third["project_id"] != project_id
    assert third.get("duplicate") is not True


def test_create_project_and_tasks():
    c = TestClient(app)
    r = c.post(
        "/projects",
        json={"objective": "Research whether a European football trips business can obtain match tickets without purchasing inventory upfront."},
    )
    assert r.status_code == 200
    pid = r.json()["project_id"]
    import time
    time.sleep(6)
    proj = c.get(f"/projects/{pid}").json()
    assert len(proj["tasks"]) >= 1
    s = c.get("/state").json()
    assert any(e["type"] in ("agent.using_tool", "agent.researching", "task.created", "package.created") for e in s["events"])
    assert len(s.get("work_packages") or proj.get("work_packages") or []) >= 1


def test_approval_and_failure_recovery():
    c = TestClient(app)
    c.post("/demo/fail")
    s = c.get("/state").json()
    assert any(a["status"] == "error" for a in s["agents"])
    c.post("/demo/retry")
    s = c.get("/state").json()
    researcher = next(a for a in s["agents"] if a["id"] == "web-researcher")
    assert researcher["status"] == "idle"
