"""Campus acceptance against a local Ollama. This does not build or launch Milo.

The workflow sets the model, the database, and the access key. A missing
model is a failure. The stub is not used as a substitute.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
OUT = Path(os.environ.get("AYVEN_ACCEPTANCE_DIR", ROOT / "acceptance-artifacts"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("AYVEN_ACCEPTANCE_PORT", "8844"))
BASE = f"http://{HOST}:{PORT}"
OLLAMA = os.environ.get("AYVEN_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
MODEL = os.environ.get("AYVEN_EMPLOYEE_MODEL", "qwen3:4b")
KEY = os.environ.get("AYVEN_ACCESS_KEY", "")
JOB_TIMEOUT = int(os.environ.get("AYVEN_ACCEPTANCE_JOB_TIMEOUT_S", "5400"))
# REPAIRING is where a manager RETURN stops. The worker has finished; polling it is not a running job.
TERMINAL = {"COMPLETED", "FAILED", "UNRESOLVED", "AWAITING_APPROVAL", "AWAITING_CLARIFICATION", "REPAIRING"}
SECTIONS = (
    "Service",
    "Target customer",
    "Problem",
    "Offer and positioning",
    "Competitor and market research",
    "Pricing",
    "Channels",
    "Advert",
    "Call to action",
    "Next steps",
    "Assumptions",
    "Unresolved",
)


class Client:
    def __init__(self) -> None:
        self.cookie = ""

    def request(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> tuple[int, dict | str]:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if auth and self.cookie:
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=JOB_TIMEOUT) as response:
                raw = response.read().decode()
                self._take_cookie(response.headers.get("Set-Cookie") or "")
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = raw
            return exc.code, parsed

    def _take_cookie(self, header: str) -> None:
        if "ayven_session=" in header:
            self.cookie = header.split(";", 1)[0]

    def get(self, path: str, auth: bool = True):
        return self.request("GET", path, auth=auth)

    def post(self, path: str, body: dict | None = None, auth: bool = True):
        return self.request("POST", path, body or {}, auth=auth)


def base_env(db: Path, research_mode: str) -> dict:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": f"{ROOT}{os.pathsep}{API}",
        "AYVEN_DB": str(db),
        "AYVEN_LLM_STUB": "0",
        "AYVEN_ALLOW_ESCALATION": "0",
        "AYVEN_RESEARCH_MODE": research_mode,
        "AYVEN_LOCAL_LLM_BASE_URL": OLLAMA,
        "AYVEN_EMPLOYEE_MODEL": MODEL,
        "AYVEN_SUPERVISOR_MODEL": MODEL,
        "AYVEN_MANAGER_MODEL": MODEL,
        "AYVEN_LLM_TIMEOUT_S": os.environ.get("AYVEN_LLM_TIMEOUT_S", "600"),
        "AYVEN_STAGE_TIMEOUT_S": os.environ.get("AYVEN_STAGE_TIMEOUT_S", "900"),
        "AYVEN_MAX_RESEARCH_ROUNDS": os.environ.get("AYVEN_MAX_RESEARCH_ROUNDS", "1"),
        "AYVEN_MAX_ATTEMPTS": os.environ.get("AYVEN_MAX_ATTEMPTS", "1"),
        "AYVEN_OLLAMA_THINK": "0",
    })
    for name in ("AYVEN_LLM_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
        env.pop(name, None)
    return env


def start_server(env: dict, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=API,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    proc._ayven_log = handle  # type: ignore[attr-defined]
    return proc


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    handle = getattr(proc, "_ayven_log", None)
    if handle:
        handle.close()


def wait_health(timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def login(client: Client) -> None:
    if not KEY:
        return
    status, _body = client.post("/login", {"key": KEY}, auth=False)
    if status != 200:
        raise RuntimeError(f"login failed: {status}")


def parent_package(project: dict) -> dict:
    parents = [row for row in project.get("work_packages") or [] if not row.get("parent_id")]
    parents.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    for row in parents:
        if row.get("workflow_state"):
            return row
    return parents[0] if parents else {}


def wait_job(client: Client, project_id: str, timeout: int = JOB_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        status, project = client.get(f"/projects/{project_id}")
        if status == 200 and isinstance(project, dict):
            last = parent_package(project)
            if (last.get("workflow_state") or "") in TERMINAL:
                return last
        time.sleep(8)
    raise TimeoutError(f"job {project_id} stayed at {last.get('workflow_state')}")


def ollama_ready() -> tuple[bool, str]:
    url = OLLAMA.rstrip("/").removesuffix("/v1") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    names = [item.get("name") or "" for item in payload.get("models") or []]
    if any(name == MODEL or name.startswith(MODEL + ":") or MODEL.startswith(name) for name in names):
        return True, ", ".join(names)
    return False, "models present: " + (", ".join(names) or "none")


def write_pdf(path: Path, text: str) -> None:
    lines = []
    for raw in text.splitlines() or [" "]:
        chunk = raw.replace("\\", " ").replace("(", " ").replace(")", " ")
        while chunk:
            lines.append(chunk[:90])
            chunk = chunk[90:]
    if not lines:
        lines = [" "]
    commands = ["BT", "/F1 10 Tf", "50 780 Td"]
    for index, line in enumerate(lines[:80]):
        if index:
            commands.append("0 -14 Td")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Count 1 /Kids [3 0 R] >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n",
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream endobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    blob = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for item in objects:
        offsets.append(len(blob))
        blob.extend(item)
    xref = len(blob)
    blob.extend(f"xref\n0 {len(offsets)}\n".encode())
    blob.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        blob.extend(f"{offset:010d} 00000 n \n".encode())
    blob.extend(f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(blob)


def _plan_can_complete(package: dict, view: dict) -> bool:
    """COMPLETED requires relevant pages and filled sections, not an empty template."""
    try:
        from app.intelligence.deliverable import business_can_complete
    except Exception:
        return False
    findings = (package or {}).get("findings") or ""
    objective = (package or {}).get("objective") or (view or {}).get("objective") or ""
    answer = ""
    obs = {}
    raw = (package or {}).get("observability_json") or ""
    if raw:
        try:
            obs = json.loads(raw)
        except json.JSONDecodeError:
            obs = {}
    understood = str(obs.get("understood_objective") or objective)
    if not understood and answer:
        understood = answer
    previews = obs.get("evidence_preview")
    if not isinstance(previews, list):
        previews = []
    return business_can_complete(findings, previews, understood)


def save_vertical(findings: str, view: dict) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    markdown = findings or "No findings were recorded."
    (OUT / "alba-kitchen-refresh.md").write_text(markdown, encoding="utf-8")
    evidence = view.get("evidence") or []
    rows = "".join(
        f"<li><b>{item.get('provenance') or 'UNRESOLVED'}</b> {item.get('sentence') or item.get('claim') or ''}</li>"
        for item in evidence
    )
    html = (
        "<html><head><meta charset='utf-8'><title>Alba Kitchen Refresh</title></head><body>"
        f"<h1>Alba Kitchen Refresh</h1><pre>{markdown}</pre><h2>Evidence</h2><ul>{rows}</ul>"
        f"<p>Cost: {view.get('cost')}</p></body></html>"
    )
    (OUT / "alba-kitchen-refresh.html").write_text(html, encoding="utf-8")
    write_pdf(OUT / "alba-kitchen-refresh.pdf", markdown)
    return {
        "markdown": str(OUT / "alba-kitchen-refresh.md"),
        "html": str(OUT / "alba-kitchen-refresh.html"),
        "pdf": str(OUT / "alba-kitchen-refresh.pdf"),
    }


def record(rows: list, number: int, name: str, status: str, detail: str) -> None:
    rows.append({"item": number, "name": name, "status": status, "detail": detail})
    print(f"{number:02d} {status} {name}: {detail}", flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    ready, detail = ollama_ready()
    record(rows, 4, "Campus can see Ollama", "PASS" if ready else "FAIL", detail)
    if not ready:
        (OUT / "acceptance-report.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return 1

    record(rows, 1, "Milo.exe present", "OUT_OF_SCOPE", "The Milo repo agent builds the Windows package. This run is Campus alone.")
    record(rows, 2, "Packaged Milo launches", "OUT_OF_SCOPE", "Not part of the Campus runner.")

    db_fixture = OUT / "fixture.db"
    db_live = OUT / "live.db"
    client = Client()
    proc = start_server(base_env(db_fixture, "fixtures"), OUT / "fixture-server.log")
    try:
        if not wait_health():
            record(rows, 3, "Campus starts", "FAIL", "fixture server did not answer /health")
            return _finish(rows, 1)
        record(rows, 3, "Campus starts", "PASS", BASE)
        blocked, _ = client.get("/state", auth=False)
        record(rows, 22, "Auth protection", "PASS" if blocked == 401 else "FAIL", f"unauthenticated /state returned {blocked}")
        login(client)
        opened, _ = client.get("/health")
        if opened != 200:
            record(rows, 22, "Auth health stays open", "FAIL", str(opened))
        status, project = client.post("/projects", {"objective": "Research the public market for office supplies and name the gaps.", "title": "fixture approve"})
        if status != 200:
            record(rows, 15, "Fixture approval", "FAIL", f"create {status}")
        else:
            package = wait_job(client, project["project_id"])
            if package.get("workflow_state") != "AWAITING_APPROVAL":
                record(rows, 15, "Fixture approval", "FAIL", package.get("workflow_state") or "no state")
            else:
                _code, state = client.get("/state")
                approval = next((item for item in state.get("approvals") or [] if item.get("status") == "pending"), None)
                if not approval:
                    record(rows, 15, "Fixture approval", "FAIL", "no pending approval")
                else:
                    code, _body = client.post(f"/approvals/{approval['id']}/resolve", {"decision": "approved"})
                    view_code, view = client.get(f"/campus/view?package_id={package['id']}")
                    landed = view.get("stage") if view_code == 200 else ""
                    record(rows, 15, "Fixture approval is unresolved", "PASS" if code == 200 and view.get("unresolved") else "FAIL", f"http {code} stage {landed}")
        status, project = client.post("/projects", {"objective": "Research the public market for office supplies and name the gaps.", "title": "fixture reject"})
        if status != 200:
            record(rows, 16, "Reject", "FAIL", f"create {status}")
        else:
            package = wait_job(client, project["project_id"])
            _code, state = client.get("/state")
            approval = next((item for item in state.get("approvals") or [] if item.get("status") == "pending" and item.get("project_id") == project["project_id"]), None)
            if package.get("workflow_state") != "AWAITING_APPROVAL" or not approval:
                record(rows, 16, "Reject gives FAILED", "FAIL", package.get("workflow_state") or "no approval")
            else:
                client.post(f"/approvals/{approval['id']}/resolve", {"decision": "rejected"})
                _view_code, view = client.get(f"/campus/view?package_id={package['id']}")
                record(rows, 16, "Reject gives FAILED", "PASS" if view.get("rejected") else "FAIL", view.get("stage") or "")
    except Exception as exc:
        record(rows, 15, "Fixture phase", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        stop_server(proc)

    proc = start_server(base_env(db_live, "live"), OUT / "live-server.log")
    try:
        if not wait_health():
            record(rows, 5, "Qwen answers", "FAIL", "live server did not start")
            return _finish(rows, 1)
        login(client)
        status, project = client.post("/projects", {"objective": "Calculate 6 * 7", "title": "six seven"})
        package = wait_job(client, project["project_id"]) if status == 200 else {}
        findings = package.get("findings") or ""
        calls = []
        _code, state = client.get("/state")
        if isinstance(state, dict):
            calls = (state.get("intelligence") or {}).get("model_calls") or []
        local_call = any(
            call.get("model_id") == MODEL and call.get("backend") in {"local_openai_compat", "qwen-agent"}
            for call in calls
        )
        stub_text = "stub synthesis" in findings.lower() or "live model not configured" in findings.lower()
        record(rows, 17, "Calculate 6 * 7 is 42", "PASS" if "42.00" in findings else "FAIL", findings[:180])
        record(rows, 5, "Qwen answers a real request", "PASS" if findings and not stub_text and local_call else "FAIL", f"local_call={local_call} stub_text={stub_text}")
        record(rows, 6, "Answer is not a fixture", "PASS" if findings and not stub_text else "FAIL", "calculation text checked for stub wording")
        record(rows, 21, "No paid model, GPU, or escalation", "PASS" if not any(call.get("backend") == "frontier" for call in calls) else "FAIL", "escalation calls absent" if not any(call.get("backend") == "frontier" for call in calls) else "frontier call recorded")

        status, project = client.post("/projects", {"objective": "Create a business launch plan.", "title": "alba"})
        if status != 200:
            record(rows, 7, "Vague request", "FAIL", f"create {status}")
            return _finish(rows, 1)
        paused = wait_job(client, project["project_id"])
        record(rows, 7, "Vague request gets a clarification", "PASS" if paused.get("workflow_state") == "AWAITING_CLARIFICATION" else "FAIL", paused.get("workflow_state") or "")
        same_id = paused.get("id")
        code, continued = client.post(
            f"/work-packages/{same_id}/clarification",
            {"answer": (
                "Alba Kitchen Refresh, a local kitchen painting service in the UK. "
                "Cover the service, the customer, the problem, the offer, competitor research from real websites, "
                "pricing assumptions, channels, ad copy, a call to action, next steps, evidence, assumptions, and unresolved items."
            )},
        )
        if code != 200:
            record(rows, 8, "Answer continues the same package", "FAIL", f"http {code}")
            return _finish(rows, 1)
        finished = wait_job(client, project["project_id"])
        record(rows, 8, "Answer continues the same package", "PASS" if finished.get("id") == same_id else "FAIL", finished.get("id") or "")
        _view_code, view = client.get(f"/campus/view?package_id={same_id}")
        obs_mode = ""
        pages = []
        if finished.get("observability_json"):
            try:
                obs = json.loads(finished["observability_json"])
            except json.JSONDecodeError:
                obs = {}
            obs_mode = obs.get("research_mode") or ""
            pages = [page for page in obs.get("pages") or [] if isinstance(page, str) and page.startswith("http")]
        record(rows, 9, "Live research runs", "PASS" if obs_mode == "live" else "FAIL", obs_mode or "missing")
        evidence = view.get("evidence") or []
        live_rows = [row for row in evidence if row.get("provenance") == "LIVE" and "http" in (row.get("sentence") or "")]
        record(rows, 10, "Real pages are opened", "PASS" if pages or live_rows else "FAIL", f"pages={len(pages)} live_rows={len(live_rows)}")
        record(rows, 11, "Evidence has URLs and extracted content", "PASS" if live_rows and "UNKNOWN · UNKNOWN" not in json.dumps(evidence) else "FAIL", f"live_rows={len(live_rows)}")
        record(rows, 14, "Cost is unknown when unmeasured", "PASS" if view.get("cost") == "unknown" else "FAIL", str(view.get("cost")))
        if finished.get("workflow_state") == "AWAITING_APPROVAL":
            _code, state = client.get("/state")
            approval = next((item for item in (state.get("approvals") or []) if item.get("status") == "pending" and item.get("project_id") == project["project_id"]), None)
            if not approval:
                record(rows, 12, "Approve genuine research", "FAIL", "no pending approval")
            else:
                client.post(f"/approvals/{approval['id']}/resolve", {"decision": "approved"})
                _view_code, view = client.get(f"/campus/view?package_id={same_id}")
                landed = view.get("stage")
                valid = _plan_can_complete(finished, view)
                if valid and view.get("finished") and not view.get("unresolved"):
                    record(rows, 12, "Approve genuine research", "PASS", landed or "")
                    record(rows, 13, "COMPLETED only with valid evidence", "PASS", landed or "")
                elif not valid and view.get("unresolved"):
                    record(rows, 12, "Approve genuine research", "PASS", "approved")
                    record(rows, 13, "COMPLETED only with valid evidence", "PASS", "unresolved because the evidence or the plan was not usable")
                else:
                    record(rows, 12, "Approve genuine research", "FAIL", f"stage={landed} valid={valid}")
                    record(rows, 13, "COMPLETED only with valid evidence", "FAIL", f"finished={view.get('finished')} unresolved={view.get('unresolved')} valid={valid}")
        else:
            record(rows, 12, "Approve genuine research", "FAIL", finished.get("workflow_state") or "did not wait")
            record(rows, 13, "COMPLETED only with valid evidence", "FAIL", "approval gate was not reached")
        _status, project_now = client.get(f"/projects/{project['project_id']}")
        raw_package = parent_package(project_now) if isinstance(project_now, dict) else {}
        findings = raw_package.get("findings") or finished.get("findings") or ""
        artifacts = save_vertical(findings, view if isinstance(view, dict) else {})
        missing = [name for name in SECTIONS if name not in findings]
        usable = _plan_can_complete(raw_package or finished, view if isinstance(view, dict) else {}) and not missing and "UNKNOWN · UNKNOWN" not in findings
        record(rows, 23, "Vertical slice is usable", "PASS" if usable else "FAIL", "missing " + ", ".join(missing) if missing else json.dumps(artifacts))

        stop_server(proc)
        proc = start_server(base_env(db_live, "live"), OUT / "restart-server.log")
        restarted = wait_health()
        login(client)
        _code, again = client.get(f"/campus/view?package_id={same_id}")
        record(rows, 18, "Restart Campus", "PASS" if restarted else "FAIL", "health after restart" if restarted else "health failed")
        record(rows, 19, "Saved work remains", "PASS" if again.get("package_id") == same_id else "FAIL", again.get("package_id") or "")
        _code, state = client.get("/state")
        tools = [item.get("tool") for item in (state.get("intelligence") or {}).get("tool_calls") or []]
        sent = [tool for tool in tools if tool in ("send_email", "purchase", "external_contact")]
        record(rows, 20, "No external action was sent", "PASS" if not sent else "FAIL", ", ".join(sent) or "none")
        _health_code, health = client.get("/health")
        escalation = ((health.get("workforce") or {}).get("escalation_enabled") if isinstance(health, dict) else None)
        if escalation:
            record(rows, 21, "No paid model, GPU, or escalation", "FAIL", "escalation enabled")
    except Exception as exc:
        record(rows, 0, "Live phase", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        stop_server(proc)
    return _finish(rows, 0)


def _finish(rows: list[dict], code: int) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failed = [row for row in rows if row["status"] == "FAIL"]
    (OUT / "acceptance-report.json").write_text(json.dumps({"failed": len(failed), "items": rows}, indent=2), encoding="utf-8")
    lines = ["# Campus acceptance", ""]
    for row in rows:
        lines.append(f"- {row['item']:02d} {row['status']}: {row['name']} — {row['detail']}")
    (OUT / "acceptance-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"acceptance failed={len(failed)}", flush=True)
    return 1 if failed or code else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "acceptance-crash.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
