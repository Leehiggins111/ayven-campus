import json
import pytest
from app import models
from app.intelligence.delivery_plan import plan_delivery
from app.intelligence.planner import build_plan


@pytest.fixture(autouse=True)
def reset_generator():
    yield
    models.set_role_generator(None)


def test_plan_uses_requested_output_without_authorising_external_actions():
    models.set_role_generator(lambda *args: (json.dumps({"title": "Five kitchen refresh posts", "output_kind": "marketing_drafts", "requirements": ["Five complete posts", "Use supplied services", "Do not expose Lee's base"], "needs_current_sources": False}), 40, {"backend": "test"}))
    plan, meta = plan_delivery("Write five posts using my supplied service details", build_plan("request", "web_research", []))
    assert plan["requirements"][0] == "Five complete posts"
    assert "research" not in plan["stages"]
    assert plan["children"][0]["focus"] == "deliverable"
    assert plan["required_tools"] == []
    assert not plan["completion_contract"]["action_permissions"]["send"]
    assert meta["completion_tokens"] == 40


def test_current_prices_require_research():
    models.set_role_generator(lambda *args: (json.dumps({"title": "Supplier comparison", "output_kind": "comparison", "requirements": ["Prices and delivery terms"], "needs_current_sources": True}), 40, {}))
    plan, _ = plan_delivery("Compare suppliers", build_plan("request", "business_research", []))
    assert "research" in plan["stages"]
    assert plan["required_tools"] == ["web_search", "fetch_page"]


def test_invalid_model_plan_fails_instead_of_inventing_a_deliverable():
    models.set_role_generator(lambda *args: ('{"title":"Fake", "output_kind":"send_email", "requirements":[], "needs_current_sources":false}', 10, {}))
    with pytest.raises(ValueError):
        plan_delivery("Send mail", build_plan("request", "web_research", []))


def test_review_rejects_missing_duplicate_and_invented_items():
    from app.intelligence.delivery_plan import review_delivery
    plan = {"requirements": ["First post", "Second post"]}
    for payload in [
        {"items": [{"item_number": 1, "fulfilled": True, "reason": "present"}]},
        {"items": [{"item_number": 1, "fulfilled": True, "reason": "present"}, {"item_number": 1, "fulfilled": True, "reason": "duplicate"}]},
        {"items": [{"item_number": 1, "fulfilled": True, "reason": "present"}, {"item_number": 2, "fulfilled": True, "reason": "present"}], "unsupported_claims": ["Invented testimonial"]},
    ]:
        models.set_role_generator(lambda *args: (json.dumps(payload), 30, {}))
        assert not review_delivery("Write posts", plan, "A draft", [])["passed"]


@pytest.mark.parametrize("fulfilled,expected", [(True, "COMPLETED"), (False, "FAILED")])
def test_requested_deliverable_is_published_and_missing_work_cannot_complete(monkeypatch, fulfilled, expected):
    import uuid
    from app.db import connect, init_db
    from app.intelligence.execution import run_objective
    monkeypatch.setenv("AYVEN_LLM_STUB", "0")
    monkeypatch.setenv("AYVEN_AGENT_RUNTIME", "native")
    monkeypatch.setenv("AYVEN_USE_QWEN_AGENT", "0")
    def generator(role, system, user, limit):
        if "Plan the requested deliverable" in system:
            text = json.dumps({"title": "Kitchen refresh post", "output_kind": "marketing_drafts", "requirements": ["One complete kitchen refresh post"], "needs_current_sources": False})
        elif "Check the delivered work" in system:
            text = json.dumps({"items": [{"item_number": 1, "fulfilled": fulfilled, "reason": "Draft checked"}]})
        elif role == "EMPLOYEE":
            text = "RECOMMENDATION: Give your kitchen a fresh look with replacement doors, worktops and handles. Message Alba Kitchen Refresh to discuss your project."
        elif role == "SUPERVISOR":
            text = "ACCEPT\nRationale: uses supplied service details."
        else:
            text = "SYNTHESISE\nRationale: requested draft is ready; nothing is being published."
        return text, 40, {"backend": "test"}
    models.set_role_generator(generator)
    conn = connect(); init_db(conn); conn.close()
    project = str(uuid.uuid4())
    objective = "Write a kitchen refresh post for Alba Kitchen Refresh. We replace doors, worktops and handles."
    conn = connect()
    conn.execute("INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)", (project, "Draft", objective, "running", "2026-10-09"))
    conn.commit(); conn.close()
    run_objective(project, objective)
    conn = connect()
    row = conn.execute("SELECT workflow_state,findings FROM work_packages WHERE project_id=? AND parent_id IS NULL", (project,)).fetchone()
    assert row["workflow_state"] == expected
    assert "Give your kitchen a fresh look" in row["findings"]
    conn.close()
