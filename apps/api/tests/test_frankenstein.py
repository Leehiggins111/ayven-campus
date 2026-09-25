"""Integration tests for the Frankenstein runtime.

INTEGRATION TESTED: imports, tool calls, local browser, MCP stdio, sandbox,
skills, memory, registry, fallbacks.
MODEL INTELLIGENCE UNTESTED: no GPU and no paid API. Stub text is not Qwen quality.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.db import connect
from app.intelligence.assertions import FOOTBALL, TRADES, VENDING
from app.intelligence.capabilities import available_names, frankenstein_status
from app.intelligence.code_sandbox import isolation_level, run_code
from app.intelligence.execution import Programme
from app.intelligence.fallbacks import on_http_result, on_mcp_down, on_sandbox_unavailable
from app.intelligence.mcp_boundary import call_tool, discover, prepare_call
from app.intelligence.memory import format_for_prompt, remember, retrieve
from app.intelligence.permissions import EXTERNAL_CONTACT, ROLE_CAPS
from app.intelligence.qwen_adapter import employee_turn, run_tool_loop
from app.intelligence.registry import route_for
from app.intelligence.research import research
from app.intelligence.resolution import apply_manager_veto, parse_manager_decision
from app.intelligence.skills import discover as discover_skills
from app.intelligence.skills import select_skills, skill_prompt
from app.intelligence.specialists import consider, matrix
from app.intelligence.think import strip_think
from app.intelligence.toolkit import ToolResult, now
from app.models import set_role_generator


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:40], objective, "running", "2026-09-25T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def test_qwen_agent_invokes_ayven_tools_and_denies_code():
    called = []

    def handler(tool, payload):
        called.append((tool, payload))
        return "handled:" + tool

    out = run_tool_loop(
        system="### Skill internal-door-quoting\nRecommended tools: calculator",
        user="Ayven tool runtime",
        agent_id="research-e1",
        package_id="pkg-qwen",
        preset_text='TOOL ayven_tool {"tool":"record_review","payload":"reviewed the ledger"}\nWorking notes recorded.',
        handler=handler,
    )
    assert out["runtime"] == "qwen-agent"
    assert called == [("record_review", "reviewed the ledger")]
    assert "TOOL " not in out["text"]
    assert "internal-door-quoting" in out["system_seen"]
    assert "Recommended tools: calculator" in out["system_seen"]

    called.clear()
    denied = run_tool_loop(
        system="sys",
        user="u",
        agent_id="research-e1",
        package_id="pkg-qwen",
        scripted_calls=[{"name": "code_exec", "arguments": "{\"payload\":\"print(1)\"}"}],
        handler=handler,
        preset_text="should not run",
    )
    assert denied["tools"][0]["status"] == "denied"
    assert called == []


def test_qwen_failure_falls_back_to_native_text():
    import app.intelligence.qwen_adapter as adapter

    original = adapter.run_tool_loop

    def boom(**kwargs):
        raise RuntimeError("runtime down")

    adapter.run_tool_loop = boom
    try:
        turned = employee_turn(
            system="sys",
            user="u",
            agent_id="research-e1",
            package_id="pkg",
            preset_text='TOOL ayven_tool {"tool":"record_review","payload":"x"}\nvisible prose',
        )
    finally:
        adapter.run_tool_loop = original
    assert turned["runtime"] == "native"
    assert turned["text"] == "visible prose"
    assert "runtime down" in turned["fallback"]


def test_skills_select_and_reach_the_employee_prompt():
    catalogue = discover_skills()
    assert catalogue and all(skill.loaded is False and skill.body == "" for skill in catalogue)
    door = select_skills("internal_door_quote", TRADES)
    football = select_skills("football_tickets", FOOTBALL)
    vending = select_skills("vending_prospects", VENDING)
    assert [skill.name for skill in door] == ["internal-door-quoting", "calculation", "verify-claims"]
    assert [skill.name for skill in football] == ["football-ticket-research", "research-web", "verify-claims"]
    assert [skill.name for skill in vending] == ["vending-prospect-research", "business-research", "verify-claims"]
    assert "football-ticket-research" not in skill_prompt(door)
    assert "internal-door-quoting" not in skill_prompt(football)
    assert "vending-prospect-research" not in skill_prompt(football)
    assert "Recommended tools: calculator" in skill_prompt(door)
    assert "Requested permissions" in skill_prompt(door)

    programme = Programme(_project(TRADES), TRADES, None)
    programme.prepare_all()
    system = programme.prompts("EMPLOYEE")[0]["system"]
    user = programme.prompts("EMPLOYEE")[0]["user"]
    assert "internal-door-quoting" in system
    assert "Recommended tools: calculator" in system
    assert "football-ticket-research" not in system
    assert "Ayven tool runtime" in user
    supervisor = programme.prompts("SUPERVISOR")[0]["user"]
    assert "Ayven tool runtime" not in supervisor


def test_memory_retrieval_keeps_relevant_and_drops_irrelevant():
    subject = "doors-" + uuid.uuid4().hex[:8]
    remember("DOMAIN", subject, "Livingston hinge assumption stays unproven until a survey.", provenance="package:old", tags="doors")
    remember("DOMAIN", subject, "Sourdough needs a long ferment and a Dutch oven.", provenance="package:other", tags="cooking")
    expired = remember("DOMAIN", subject, "Livingston hinge prices from a stale flyer.", provenance="package:old", tags="doors", expires_at="2000-01-01T00:00:00+00:00")
    fresh = remember("DOMAIN", subject, "Livingston hinge count is still an assumption.", provenance="package:new", tags="doors", supersedes=expired)
    rows = retrieve("Livingston hinge assumption for internal doors", subject_id=subject)
    blob = format_for_prompt(rows)
    assert "hinge assumption stays unproven" in blob or "hinge count is still an assumption" in blob
    assert "Sourdough" not in blob
    assert "stale flyer" not in blob
    assert fresh
    programme = Programme(_project(TRADES), TRADES, None)
    programme.prepare_all()
    user = programme.prompts("EMPLOYEE")[0]["user"]
    assert "stays unproven" in user
    assert "Sourdough" not in user


def test_agentic_review_records_a_gap_and_one_next_query():
    def reviewer(user):
        assert "research review" in user
        return "I still don't know the decision-maker.\nNEXT: adversarial-zero-results-zz"

    result = research(
        "general",
        "find a page",
        "pkg-review",
        "research-e3",
        queries=["adversarial-stale"],
        max_rounds=1,
        reviewer=reviewer,
    )
    assert any("decision-maker" in gap.lower() or "don't know" in gap.lower() for gap in result["gaps"])
    assert "adversarial-zero-results-zz" in result["queries"]
    assert result["memory_fallback_used"] is False


def test_http_success_does_not_launch_the_browser_and_js_wall_can():
    launched = []

    def browse(agent, package, url):
        launched.append(url)
        return ToolResult(tool="browser", status="ok", source_url=url, extracted_content="rendered after javascript", timestamp=now())

    quiet = research("general", "x", "pkg-http", "research-e3", queries=["adversarial-stale"], max_rounds=1, browse_fn=browse)
    assert launched == []
    assert quiet["evidence"]
    wall = research("general", "x", "pkg-js", "research-e3", queries=["adversarial-js-wall"], max_rounds=1, browse_fn=browse)
    assert launched
    assert any((item.get("metadata") or {}).get("via") == "browser" for item in wall["evidence"])
    blocked = on_http_result(status="error", error="js_wall", browser_available=False, browser_permitted=True)
    assert blocked["launch_browser"] is False
    assert "No model-memory fallback" in blocked["gap"]
    assert on_http_result(status="ok", error="", browser_available=True, browser_permitted=True)["launch_browser"] is False
    assert on_mcp_down("local", "connection refused")["tools"] == "unavailable"
    assert on_sandbox_unavailable()["use"] == "calculator"


def test_browser_use_reads_a_local_http_page():
    from app.intelligence.browser_adapter import available, open_page

    if not available():
        raise AssertionError("browser integration expected Chrome and browser-use on this machine")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body><h1>AYVEN_BROWSER_MARKER</h1><p>read only</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = open_page("research-e3", "pkg-browser", f"http://127.0.0.1:{port}/")
        denied = open_page("research-e1", "pkg-browser", f"http://127.0.0.1:{port}/")
        blocked = open_page("research-e3", "pkg-browser", "file:///tmp/secret.html")
        purchase = open_page("research-e3", "pkg-browser", f"http://127.0.0.1:{port}/checkout")
    finally:
        server.shutdown()
    assert result.status == "ok"
    assert "AYVEN_BROWSER_MARKER" in result.extracted_content
    assert denied.status == "denied"
    assert blocked.status == "denied"
    assert purchase.status == "denied"


def test_mcp_stdio_round_trip_and_approval_gate():
    import sys

    os.environ["AYVEN_MCP_SERVERS"] = json.dumps([{
        "name": "ayven-local",
        "command": sys.executable,
        "args": ["-m", "app.intelligence.mcp_local_server"],
    }])
    try:
        found = discover()
        assert found["ok"] is True
        assert "ayven_lookup" in found["tools"]
        assert "ayven_send" in found["tools"]
        read = call_tool("research-e3", "ayven_lookup", {"topic": "doors"})
        assert read["ok"] is True
        assert "AYVEN_MCP_OK:doors" in read["result"]
        assert read["kind"] == "read"
        blocked = prepare_call("research-e1", "ayven_send")
        assert blocked["ok"] is False
        assert blocked["kind"] == "action"
        ROLE_CAPS["probe-agent"] = {EXTERNAL_CONTACT}
        gated = prepare_call("probe-agent", "ayven_send", approved=False)
        assert gated["status"] == "approval_required"
        down = call_tool("research-e3", "ayven_lookup", {"topic": "x"}, server={"name": "down", "command": sys.executable, "args": ["-m", "app.intelligence.missing_mcp_server"]})
        assert down["ok"] is False
        assert down["status"] == "unavailable"
    finally:
        os.environ.pop("AYVEN_MCP_SERVERS", None)
        ROLE_CAPS.pop("probe-agent", None)


def test_code_sandbox_runs_and_blocks_network():
    previous = os.environ.get("AYVEN_ALLOW_CODE")
    os.environ["AYVEN_ALLOW_CODE"] = "1"
    try:
        assert isolation_level() == "unshare-user-net-pid"
        denied = run_code("research-e1", "print(1)\n", approved=True)
        assert denied.status == "denied"
        unapproved = run_code("research-e3", "print(1)\n", approved=False)
        assert unapproved.status == "denied"
        ran = run_code("research-e3", "print(1+1)\n", approved=True)
        assert ran.status == "ok"
        assert ran.extracted_content.strip() == "2"
        assert ran.metadata["sandbox"] == "unshare-user-net-pid"
        networked = run_code(
            "research-e3",
            "import urllib.request\nurllib.request.urlopen('http://example.com', timeout=3)\n",
            approved=True,
        )
        assert networked.status == "error"
    finally:
        if previous is None:
            os.environ.pop("AYVEN_ALLOW_CODE", None)
        else:
            os.environ["AYVEN_ALLOW_CODE"] = previous
    os.environ["AYVEN_ALLOW_CODE"] = "0"
    disabled = run_code("research-e3", "print(1)\n", approved=True)
    assert disabled.status == "disabled"
    assert "calculator" in disabled.error.lower()


def test_routing_records_why_and_registry_hides_unavailable_tools():
    calc = route_for("internal_door_quote", "calculation")
    browser = route_for("football_tickets", "browser")
    sandbox = route_for("web_research", "code_sandbox")
    coding = route_for("web_research", "coding")
    general = route_for("football_tickets", "draft")
    assert calc["model_id"] == "ayven-calculator"
    assert "deterministic" in calc["reason"]
    assert "HTTP" in browser["reason"] or "browser" in browser["reason"].lower()
    assert sandbox["model_id"] == "ayven-code-sandbox"
    assert "calculator" in sandbox["reason"]
    assert "coding-capable" in coding["reason"]
    assert coding["model_id"] != "ayven-calculator"
    assert general["roles"] == ["EMPLOYEE"]
    alive = available_names()
    assert "calculator" in alive
    assert "qwen_agent" in alive
    assert "coding_agent" not in alive
    status = frankenstein_status()
    assert status["skills"] == "ACTIVE"
    assert status["memory"] == "ACTIVE"
    assert status["code_sandbox"] == "ACTIVE"
    assert status["all_core_active"] is True


def test_supervisor_does_not_see_employee_reasoning_and_manager_can_be_vetoed():
    captured = {}

    def gen(role, system, user, max_tokens):
        captured.setdefault(role, []).append(user)
        if role == "EMPLOYEE":
            return "EMPLOYEE_SECRET_REASONING", 4, {"backend": "generator"}
        if role == "SUPERVISOR":
            return "ACCEPT", 3, {"backend": "generator"}
        return "<think>SECRET_MANAGER</think>\nSYNTHESISE\nRationale: publish it", 4, {"backend": "generator"}

    set_role_generator(gen)
    try:
        from app.intelligence.execution import run_objective

        run_objective(_project("Say hello"), "Say hello", None)
    finally:
        set_role_generator(None)
    supervisor_users = "\n".join(captured.get("SUPERVISOR") or [])
    assert "EMPLOYEE_SECRET_REASONING" not in supervisor_users
    clean = strip_think("<think>SECRET_MANAGER</think>\nSYNTHESISE\nRationale: publish it")
    proposal, rationale = parse_manager_decision(clean)
    assert proposal == "SYNTHESISE"
    assert "SECRET_MANAGER" not in rationale
    safety = {"decision": "CLARIFY", "reason": "A person must approve.", "resolution_method": "customer_clarification"}
    vetoed = apply_manager_veto("SYNTHESISE", safety)
    assert vetoed["decision"] == "CLARIFY" and vetoed["veto"] is True
    cautious = apply_manager_veto("ESCALATE", safety)
    assert cautious["decision"] == "ESCALATE"
    returned = apply_manager_veto("RETURN", safety)
    assert returned["decision"] == "CLARIFY"


def test_specialist_boundary_does_not_install_a_coding_host():
    assert consider("internal_door_quote")["status"] == "not_applicable"
    engine = consider("software_engineering")
    assert engine["engine"] == "ayven-code-sandbox"
    rejected = {row["project"]: row["decision"] for row in matrix()}
    assert rejected["All-Hands-AI/OpenHands"] == "REJECTED"
    assert rejected["Aider-AI/aider"] == "REJECTED"
    assert rejected["letta-ai/letta"] == "REJECTED"
    assert rejected["mem0ai/mem0"] == "REJECTED"
