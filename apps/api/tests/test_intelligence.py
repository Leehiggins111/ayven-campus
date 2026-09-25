"""Local proof of the intelligence loop. No GPU and no paid API."""

import json
import os
import sqlite3
import uuid

import pytest

from app.db import connect, reset_connection_state
from app.intelligence.assertions import (
    FOOTBALL,
    TRADES,
    VENDING,
    evaluate_project,
    failed,
)
from app.intelligence.audit import authoritative_decision
from app.intelligence.calc import CalcError, eval_arithmetic
from app.intelligence.execution import Programme, run_objective
from app.intelligence.mcp_boundary import prepare_call, status as mcp_status
from app.intelligence.permissions import PermissionDenied, authorize
from app.intelligence.planner import classify
from app.intelligence.quoting import quote_internal_doors
from app.intelligence.registry import route_for
from app.intelligence.skills import discover, select_skills
from app.intelligence.think import strip_think
from app.models import complete_role, escalation_configured, set_role_generator


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-09-25T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    run_objective(project_id, objective, None)
    return project_id


def test_calculator_is_restricted():
    assert eval_arithmetic("7*82") == "574.00"
    assert eval_arithmetic("(82+18+7+12+95)*7+35") == "1533.00"
    assert eval_arithmetic("(82+18+7+12)*7+95+35") == "963.00"
    with pytest.raises(CalcError):
        eval_arithmetic("__import__('os').system('ls')")
    with pytest.raises(CalcError):
        eval_arithmetic("2**10")


def test_door_quote_preserves_ambiguity():
    quote = quote_internal_doors(TRADES)
    assert quote["door_count"] == 7
    assert quote["size_counts"] == {"762x1981": 4, "686x1981": 2, "838x1981": 1}
    assert quote["labour_unit"] == "AMBIGUOUS"
    assert quote["vat"] == "UNKNOWN_NOT_APPLIED"
    assert quote["scenarios"]["labour_per_door"]["total_ex_vat"] == "1533.00"
    assert quote["scenarios"]["labour_per_job"]["total_ex_vat"] == "963.00"
    assert quote["per_door_ex_delivery"] == "214.00"
    assert quote["is_final_quote"] is False
    assert any("handing" in field.lower() for field in quote["missing_fields"])


def test_explicit_labour_unit_is_not_ambiguous():
    per_door = quote_internal_doors("3 internal doors. door £10, labour £5 per door, delivery £1/job")
    assert per_door["labour_unit"] == "PER_DOOR"
    per_job = quote_internal_doors("3 internal doors. door £10, labour £5 per job, delivery £1/job")
    assert per_job["labour_unit"] == "PER_JOB"


def test_skills_are_selective():
    catalogue = discover()
    assert len(catalogue) >= 7
    selected = select_skills("internal_door_quote", TRADES)
    names = [skill.name for skill in selected]
    assert names == ["internal-door-quoting", "calculation", "verify-claims"]
    assert all(skill.loaded and skill.body for skill in selected)
    assert "vending-prospect-research" not in names
    joined = "\n".join(skill.body for skill in selected).lower()
    assert "per door" in joined and "per job" in joined
    assert "vat" in joined


def test_routing_keeps_frontier_off():
    assert escalation_configured() is False
    calc = route_for("internal_door_quote", "calculation")
    assert calc["model_id"] == "ayven-calculator"
    assert calc["paid_call"] is False
    escalation = route_for("football_tickets", "escalation")
    assert escalation["enabled"] is False
    text, tokens, meta = complete_role("ESCALATION", "sys", "user")
    assert meta["backend"] == "escalation_disabled"
    assert tokens == 0
    assert "disabled" in text.lower()


def test_permissions_block_contact_and_purchase():
    assert authorize("research-e1", "calculator") == "RUN_CALC"
    assert authorize("research-e1", "web_search") == "READ_WEB"
    for tool in ("send_email", "purchase", "external_contact", "code_exec", "write_file"):
        with pytest.raises(PermissionDenied):
            authorize("research-e1", tool)
    denied = prepare_call("research-e1", "send_email")
    assert denied["ok"] is False
    assert mcp_status()["posture"] in ("OPTIONAL_NOT_INSTALLED", "INSTALLED_NOT_CONNECTED", "INSTALLED_NO_SERVERS", "CONFIGURED")


def test_think_tags_do_not_survive():
    assert strip_think("<think>SECRET_COTHOUGHT</think>\nVisible") == "Visible"
    assert "SECRET_COTHOUGHT" not in strip_think("<think>SECRET_COTHOUGHT")

    def leak(role, system, user, max_tokens):
        return "<think>SECRET_COTHOUGHT</think>\nACCEPT", 12, {"backend": "generator", "model": "test"}

    set_role_generator(leak)
    try:
        project_id = _project("Say hello")
    finally:
        set_role_generator(None)
    conn = connect()
    blob = " ".join(
        " ".join(str(value) for value in dict(row).values())
        for row in conn.execute("SELECT * FROM work_packages WHERE project_id=?", (project_id,)).fetchall()
    )
    conn.close()
    assert "SECRET_COTHOUGHT" not in blob
    assert "<think" not in blob.lower()
    assert classify("Say hello") == "trivial"


def test_bad_quote_is_taken_over_not_trusted():
    assert authoritative_decision("The final quote is £1533.00.", "internal_door_quote", "scenarios", 1) == "TAKE_OVER"
    assert authoritative_decision("Tickets are available to buy now.", "football_tickets", "routes", 1) == "TAKE_OVER"


def test_frozen_benchmarks_pass_locally(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network call")

    monkeypatch.setattr("httpx.post", boom)
    monkeypatch.setattr("httpx.get", boom)
    for kind, objective in (("trades", TRADES), ("football", FOOTBALL), ("vending", VENDING)):
        project_id = _project(objective)
        results = evaluate_project(project_id, kind)
        misses = failed(results)
        assert not misses, misses


def test_supervisor_and_manager_are_stored():
    project_id = _project(TRADES)
    conn = connect()
    packages = [dict(r) for r in conn.execute("SELECT * FROM work_packages WHERE project_id=?", (project_id,)).fetchall()]
    ids = [p["id"] for p in packages]
    marks = ",".join("?" * len(ids))
    audits = [dict(r) for r in conn.execute(f"SELECT * FROM verification_results WHERE package_id IN ({marks}) AND stage='supervisor'", ids).fetchall()]
    manager = conn.execute(f"SELECT * FROM verification_results WHERE package_id IN ({marks}) AND stage='manager'", ids).fetchone()
    conn.close()
    assert len(audits) == 3
    assert all(row["decision"] in ("ACCEPT", "TAKE_OVER") for row in audits)
    payload = json.loads(manager["payload_json"])
    assert payload["received_supervisor_audits"] is True
    assert payload["frontier_called"] is False
    assert len(payload["audits"]) == 3


def test_migration_keeps_v04_rows(tmp_path, monkeypatch):
    path = tmp_path / "v04.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE work_packages (
            id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT, title TEXT, objective TEXT,
            origin TEXT, agent_id TEXT, department_id TEXT, stage TEXT, status TEXT,
            findings TEXT, next_action TEXT, requires_approval INTEGER, destination TEXT,
            error TEXT, parent_id TEXT, tier TEXT, confidence REAL, review_status TEXT,
            attempt_count INTEGER, return_reason TEXT, model_role TEXT, created_at TEXT, updated_at TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO work_packages(
            id,project_id,task_id,title,objective,origin,agent_id,department_id,stage,status,
            findings,next_action,requires_approval,destination,error,parent_id,tier,confidence,
            review_status,attempt_count,return_reason,model_role,created_at,updated_at
        ) VALUES('old','proj',NULL,'Old','keep','milo','research-e1','research','results','complete',
            'v0.4 finding',NULL,0,'command',NULL,NULL,'EMPLOYEE',NULL,NULL,0,NULL,'EMPLOYEE','t','t')"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AYVEN_DB", str(path))
    reset_connection_state()
    try:
        live = connect()
        row = live.execute("SELECT findings, task_class FROM work_packages WHERE id='old'").fetchone()
        assert row["findings"] == "v0.4 finding"
        assert row["task_class"] is None
        tables = {r[0] for r in live.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"claims", "plans", "tool_calls", "verification_results", "quality_results"} <= tables
        live.close()
    finally:
        monkeypatch.setenv("AYVEN_DB", "/tmp/ayven-pytest-intel.db")
        reset_connection_state()


def test_exam_files_match_the_frozen_strings():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "benchmarks" / "exams"
    assert (root / "A_trades.txt").read_text(encoding="utf-8").strip() == TRADES
    assert (root / "B_football.txt").read_text(encoding="utf-8").strip() == FOOTBALL
    assert (root / "C_vending.txt").read_text(encoding="utf-8").strip() == VENDING


def test_memory_is_scoped_not_a_transcript():
    project_id = _project(TRADES)
    conn = connect()
    rows = [dict(r) for r in conn.execute("SELECT * FROM memories WHERE subject_id=?", (project_id,)).fetchall()]
    conn.close()
    assert rows
    assert rows[0]["scope"] == "PROJECT"
    assert "AMBIGUOUS" in rows[0]["content"] or "ambiguous" in rows[0]["content"].lower()
    assert "<think" not in rows[0]["content"].lower()
    assert "user:" not in rows[0]["content"].lower()
