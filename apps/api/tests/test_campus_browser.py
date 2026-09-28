"""Headless campus UI against the real FastAPI app and the stub model."""

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.db import connect, reset_connection_state
from app.intelligence.claims import add_claim
from app.intelligence.execution import run_objective
from app.intelligence.repair import apply_repairs

ROOT = Path(__file__).resolve().parents[3]
API_DIR = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "campus-screenshots"
ARTIFACTS = Path("/opt/cursor/artifacts/campus-screenshots")


def _project(objective: str) -> str:
    project_id = str(uuid.uuid4())
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, objective[:48], objective, "running", "2026-09-28T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return project_id


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    target = SHOTS / name
    page.screenshot(path=str(target), full_page=False)
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        shutil.copy(target, ARTIFACTS / name)
    except OSError:
        pass


def test_campus_ui_approve_reject_clarify_repair_and_evidence():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    previous = os.environ.get("AYVEN_DB", "")
    path = Path("/tmp/ayven-campus-ui.db")
    if path.exists():
        path.unlink()
    os.environ["AYVEN_DB"] = str(path)
    reset_connection_state()
    research = "Research the public market for office supplies and name the gaps."
    try:
        approve_project = _project(research)
        approve_id = run_objective(approve_project, research, "ui-approve")
        reject_id = run_objective(_project(research), research, "ui-reject")
        clarify = "Compare public suppliers of office stationery.\nNEED: which city should the search cover?"
        clarify_id = run_objective(_project(clarify), clarify, "ui-clarify")
        claim = add_claim(approve_id, "research-e1", "Footfall is 90000.", "FACT", evidence_text="A centre exists.", source_type="UNKNOWN")
        second = add_claim(approve_id, "research-e1", "Another figure is 90000.", "FACT", evidence_text="A centre exists.", source_type="UNKNOWN")
        report = apply_repairs(approve_id, [
            {"claim_id": claim["id"], "result": "DISPROVED", "challenge": "was footfall stated", "resolution": "Footfall was not in the opened page.", "evidence": "A centre exists."},
            {"claim_id": second["id"], "result": "DISPROVED", "challenge": "was footfall stated", "resolution": "Footfall was not in the opened page.", "evidence": "A centre exists."},
        ], cap=1)
        conn = connect()
        import json
        raw = conn.execute("SELECT observability_json FROM work_packages WHERE id=?", (approve_id,)).fetchone()["observability_json"]
        obs = json.loads(raw or "{}")
        obs["repairs"] = report["actions"]
        conn.execute("UPDATE work_packages SET observability_json=? WHERE id=?", (json.dumps(obs), approve_id))
        conn.commit()
        conn.close()

        port = _free_port()
        env = os.environ.copy()
        env["AYVEN_DB"] = str(path)
        env["AYVEN_LLM_STUB"] = "1"
        env["AYVEN_ALLOW_ESCALATION"] = "0"
        env["AYVEN_RESEARCH_MODE"] = "fixtures"
        env["PYTHONPATH"] = f"{ROOT}:{API_DIR}"
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(API_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.time() + 20
            import urllib.request
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.2)
            else:
                output = server.stdout.read().decode() if server.stdout else ""
                raise AssertionError(output[-2000:])

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/campus", wait_until="domcontentloaded", timeout=90000)
                page.wait_for_selector("[data-testid='campus-glance']", timeout=90000)
                _shot(page, "01-latest-package.png")

                page.click(f"[data-testid='work-package'][data-package='{approve_id}']")
                page.wait_for_selector("[data-testid='needs-you']", timeout=20000)
                page.wait_for_selector("[data-testid='workflow-step'][data-stage='APPROVAL'][data-state='current']")
                page.wait_for_selector("[data-testid='agent-state'][data-agent='research-mgr']")
                import json
                import urllib.request
                live = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/state"))
                manager = next(agent for agent in live["agents"] if agent["id"] == "research-mgr")
                shown = page.locator("[data-testid='agent-state'][data-agent='research-mgr']").get_attribute("data-visual")
                assert shown == (manager.get("visual_state") or "IDLE")
                page.click("[data-testid='evidence-toggle']")
                page.wait_for_selector("[data-testid='evidence-claim']")
                page.locator("[data-testid='workflow']").scroll_into_view_if_needed()
                _shot(page, "02-needs-you-workflow.png")
                page.locator("[data-testid='evidence-list']").scroll_into_view_if_needed()
                _shot(page, "03-evidence.png")
                page.wait_for_selector("[data-testid='repairs']")
                page.locator("[data-testid='repairs']").scroll_into_view_if_needed()
                _shot(page, "04-repairs.png")

                page.click("[data-testid='approve']")
                page.wait_for_selector("[data-testid='final-result']", timeout=20000)
                page.wait_for_selector("[data-testid='glance-finished']")
                assert "Yes" in page.locator("[data-testid='glance-finished']").inner_text()
                assert page.locator("[data-testid='campus-glance']").get_attribute("data-package") == approve_id
                page.locator("[data-testid='final-result']").scroll_into_view_if_needed()
                _shot(page, "05-approved-complete.png")

                page.click(f"[data-testid='work-package'][data-package='{reject_id}']")
                page.wait_for_selector("[data-testid='needs-you']", timeout=20000)
                page.click("[data-testid='reject']")
                page.wait_for_selector("[data-testid='final-result']", timeout=20000)
                assert "Rejected" in page.locator("[data-testid='final-result']").inner_text()
                page.locator("[data-testid='final-result']").scroll_into_view_if_needed()
                _shot(page, "06-rejected.png")

                page.click(f"[data-testid='work-package'][data-package='{clarify_id}']")
                page.wait_for_selector("[data-testid='needs-clarification']", timeout=20000)
                assert "city" in page.locator("[data-testid='clarify-question']").inner_text()
                _shot(page, "07-needs-clarification.png")
                page.fill("[data-testid='clarify-input']", "the north office")
                page.click("[data-testid='clarify-submit']")
                page.wait_for_selector("[data-testid='needs-you'], [data-testid='final-result']", timeout=60000)
                assert page.locator("[data-testid='campus-glance']").get_attribute("data-package") == clarify_id
                assert page.locator("[data-testid='needs-clarification']").count() == 0
                _shot(page, "08-clarification-continued.png")
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
    finally:
        if previous:
            os.environ["AYVEN_DB"] = previous
        else:
            os.environ.pop("AYVEN_DB", None)
        reset_connection_state()
