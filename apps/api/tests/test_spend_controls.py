"""Spend caps, escalation lock, and the pod stop rules on a fake clock."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.access import validate_public_configuration
from app.intelligence.budget import (
    check_and_reserve,
    commit,
    month_allows_new_job,
    retries,
)
from app.intelligence.gateway import estimate_cost
from app.intelligence.gpu_lifecycle import PodClock, decide, delete_command
from validation.live_plan import plan, record_load


def test_a_job_over_its_cap_halts(monkeypatch):
    monkeypatch.setenv("AYVEN_JOB_BUDGET_USD", "0.01")
    monkeypatch.setenv("AYVEN_BUDGET_ENFORCE", "1")
    result = check_and_reserve("job-cap", "EMPLOYEE", 50_000, model_id="local")
    assert result["ok"] is False
    assert result["status"] == "BUDGET_EXCEEDED"


def test_the_monthly_cap_refuses_a_new_job(monkeypatch):
    monkeypatch.setenv("AYVEN_MONTHLY_BUDGET_USD", "1.00")
    monkeypatch.setenv("AYVEN_BUDGET_ENFORCE", "1")
    first = check_and_reserve("month-job", "EMPLOYEE", 20, model_id="local")
    assert first["ok"] is True
    commit(first["reservation_id"], input_tokens=20, output_tokens=4, amount_usd=1.00)
    allowed, reason = month_allows_new_job(2.00)
    assert allowed is False
    assert reason


def test_a_retry_is_recorded_and_counted():
    reserved = check_and_reserve("retry-job", "EMPLOYEE", 16, attempt=2, retry_of="tool", model_id="stub")
    assert reserved["ok"] is True
    commit(reserved["reservation_id"], input_tokens=8, output_tokens=2, amount_usd=0.0001)
    assert retries("retry-job") == 1


def test_escalation_disabled_makes_no_external_call(monkeypatch):
    monkeypatch.setenv("AYVEN_ALLOW_ESCALATION", "0")
    result = check_and_reserve("esc-off", "ESCALATION", 100, allow_paid=True, model_id="frontier")
    assert result["ok"] is False
    assert result["status"] == "ESCALATION_DISABLED"
    assert estimate_cost(1000, "ESCALATION") == 0.0
    assert estimate_cost(1000, "EMPLOYEE") > 0


def test_escalation_armed_without_prices_refuses_startup(monkeypatch):
    monkeypatch.setenv("AYVEN_ALLOW_ESCALATION", "1")
    monkeypatch.delenv("AYVEN_ESCALATION_USD_PER_MTOK_IN", raising=False)
    monkeypatch.delenv("AYVEN_ESCALATION_USD_PER_MTOK_OUT", raising=False)
    try:
        validate_public_configuration()
        raise AssertionError("startup must refuse unpriced escalation")
    except RuntimeError as exc:
        assert "prices" in str(exc).lower()


def test_idle_and_lifetime_caps_fire_on_a_fake_clock(monkeypatch):
    monkeypatch.setenv("AYVEN_GPU_IDLE_STOP_S", "600")
    monkeypatch.setenv("AYVEN_POD_MAX_LIFETIME_S", "10800")
    monkeypatch.setenv("AYVEN_GPU_TIER", "community")
    idle = PodClock(created_at=0, last_active_at=0, tier="community", healthy=True, healthy_at=1, in_flight=0, gpu_util_pct=0)
    idle_decision = decide(idle, now=600)
    assert idle_decision["stop"] is True
    assert idle_decision["reason"] == "idle"
    assert idle_decision["action"] == "delete"
    lifetime = PodClock(created_at=0, last_active_at=10700, tier="community", healthy=True, healthy_at=1, in_flight=1, gpu_util_pct=40)
    life = decide(lifetime, now=10800)
    assert life["reason"] == "lifetime_cap"
    assert life["action"] == "delete"
    sick = PodClock(
        created_at=0, last_active_at=100, tier="community", healthy=False, healthy_at=10,
        unhealthy_since=100, in_flight=0, gpu_util_pct=0,
    )
    failed = decide(sick, now=400)
    assert failed["reason"] == "failure"
    command = delete_command()
    assert "runpodctl pod delete" in command
    assert "pod stop" not in command


def test_live_plan_uses_the_2507_model_and_records_load_time():
    body = plan()
    assert body["model"] == "Qwen3-30B-A3B-Instruct-2507"
    assert body["engine"] == "llama-server"
    assert body["spend"] is False
    assert "--reasoning" in body["self_terminate"] or body["reasoning"] == "off"
    assert "runpodctl pod delete" in body["delete_command"]
    assert body["load"]["load_s"] is None
    assert body["paid_external_by_default"] is False
    assert body["session"]["status"] == "UNVERIFIED"
    assert body["session"]["step_3_in_session"] is False
    assert body["session"]["same_cap_usd"] == body["community_cap_usd"]
    assert "assumption" in body["session"]["bfcl_assumption"].lower()
    from validation.live_plan import steps_for_session
    assert steps_for_session(True) == [1]
    assert steps_for_session(False) == [1, 2]
    measured = record_load({"load_started": 10.0, "ready": 42.5, "first_token": 43.0})
    assert measured["load_s"] == 32.5
    assert measured["first_token_s"] == 0.5
    script = Path(__file__).resolve().parents[3] / "scripts" / "run_ayven_live.sh"
    text = script.read_text(encoding="utf-8")
    assert "LLAMA_SERVER_STEP_2" in text
    assert "Qwen3-8B" in text
    assert "AYVEN_CONFIRM_SPEND" in text
    assert "llama-server" in text or "live_plan" in text
    def commands(path: Path) -> str:
        return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#"))

    employee = commands(Path(__file__).resolve().parents[3] / "scripts" / "gpu_employee.sh")
    manager = commands(Path(__file__).resolve().parents[3] / "scripts" / "gpu_manager.sh")
    assert "--guided-decoding-backend" not in employee
    assert "--guided-decoding-backend" not in manager
    assert "--structured-outputs-config.backend xgrammar" in employee
    assert "--structured-outputs-config.backend xgrammar" in manager


def test_director_plans_need_eight_valid_out_of_ten():
    from validation.live_plan import director_plans_pass, valid_director_plan

    good = {
        "title": "Guest records note",
        "output_kind": "briefing",
        "requirements": ["One sentence from the page"],
        "needs_current_sources": True,
    }
    bad = {"title": "", "output_kind": "send_email", "requirements": [], "needs_current_sources": False}
    assert valid_director_plan(good) is True
    assert valid_director_plan(bad) is False
    assert director_plans_pass([good] * 8 + [bad] * 2) is True
    assert director_plans_pass([good] * 7 + [bad] * 3) is False
    assert director_plans_pass([good] * 8) is False


def test_live_evidence_writes_criteria_before_a_result(tmp_path, monkeypatch):
    from validation.live_session import prepare_evidence, record_result, run_confirmed_session

    dest = prepare_evidence(tmp_path)
    assert (dest / "criteria.json").is_file()
    assert not (dest / "result.json").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    refused = False
    try:
        record_result(empty, {"status": "UNVERIFIED"})
    except RuntimeError:
        refused = True
    assert refused
    record_result(dest, {"status": "UNVERIFIED"})
    assert (dest / "result.json").is_file()
    monkeypatch.delenv("AYVEN_CONFIRM_SPEND", raising=False)
    monkeypatch.delenv("RUNPOD_POD_ID", raising=False)
    assert run_confirmed_session(tmp_path / "refused") == 0
    assert not (tmp_path / "refused").exists()


def test_a_pound_budget_is_kept_exactly_in_gbp(monkeypatch):
    from app.goals import get_goal, open_goal

    monkeypatch.setenv("AYVEN_JOB_BUDGET_USD", "10.00")
    goal = open_goal("gbp-job", [], job_cap_gbp=6.25)
    assert goal["job_cap_gbp"] == 6.25
    assert goal["job_cap_currency"] == "GBP"
    assert get_goal("gbp-job")["job_cap_gbp"] == 6.25


def test_an_unsupported_budget_is_reported_not_clamped(monkeypatch):
    import pytest

    from app.goals import check_budget, get_goal, open_goal

    monkeypatch.setenv("AYVEN_JOB_BUDGET_USD", "2.00")
    with pytest.raises(ValueError, match="Unsupported budget: £6.25"):
        check_budget(job_cap_gbp=6.25)
    with pytest.raises(ValueError, match="not changed"):
        open_goal("clamp-job", [], job_cap_gbp=6.25)
    assert get_goal("clamp-job") is None
    with pytest.raises(ValueError):
        check_budget(job_cap_gbp=0)
