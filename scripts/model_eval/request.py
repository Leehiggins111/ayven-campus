"""Run one Ayven request, or rewrite its sections with the compact evidence pack."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def _connect():
    from app.db import connect, reset_connection_state

    reset_connection_state()
    return connect()


def _insert_project(objective: str, title: str) -> str:
    project_id = str(uuid.uuid4())
    conn = _connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)",
        (project_id, title[:40], objective, "running", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    conn.commit()
    conn.close()
    return project_id


def _rows(project_id: str) -> list[dict]:
    conn = _connect()
    found = [dict(row) for row in conn.execute("SELECT * FROM work_packages WHERE project_id=?", (project_id,)).fetchall()]
    conn.close()
    return found


def _draft_text(rows: list[dict]) -> str:
    for row in rows:
        if (row.get("title") or "") == "Next action":
            return row.get("findings") or ""
    for row in rows:
        findings = row.get("findings") or ""
        if "Service" in findings and "Pricing" in findings:
            return findings
    return ""


def _evidence_text(parent_id: str) -> tuple[str, list[dict]]:
    from app.intelligence.store import package

    row = package(parent_id) or {}
    try:
        obs = json.loads(row.get("observability_json") or "{}")
    except json.JSONDecodeError:
        obs = {}
    pages = []
    lines = []
    for item in obs.get("evidence_preview") or []:
        if not isinstance(item, dict):
            continue
        pages.append({
            "source_url": item.get("url") or "",
            "source_title": item.get("title") or "",
            "extracted_content": item.get("text") or "",
        })
        lines.append(item.get("url") or "")
        lines.append(item.get("title") or "")
        lines.append(item.get("text") or "")
    return "\n".join(lines), pages


def _attempts(package_ids: list[str]) -> list[dict]:
    from app.intelligence.observability import traces_for

    attempts = []
    for package_id in package_ids:
        if not package_id:
            continue
        for event in traces_for(package_id):
            if event.get("event") != "generation":
                continue
            payload = event.get("payload") or {}
            attempts.append({
                "kind": payload.get("kind") or "",
                "reason": payload.get("reason") or "",
                "response": payload.get("response") or "",
                "prompt_tokens": int(payload.get("prompt_tokens") or 0),
                "completion_tokens": int(payload.get("completion_tokens") or 0),
                "elapsed_s": payload.get("elapsed_s") or 0,
                "num_ctx": int(payload.get("num_ctx") or 0),
                "prompt_chars": int(payload.get("prompt_chars") or 0),
                "finish_reason": payload.get("finish_reason") or "",
                "attempt": int(payload.get("attempt") or 0),
                "prompt_eval_s": float(payload.get("prompt_eval_s") or 0),
                "eval_s": float(payload.get("eval_s") or 0),
                "load_s": float(payload.get("load_s") or 0),
                "error": payload.get("error") or "",
            })
    return attempts


def _logged_calls(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _objective_for(request: dict, parent_id: str) -> str:
    if request.get("answer"):
        return request["objective"].rstrip() + "\nLee answered: " + request["answer"]
    from app.intelligence.store import package

    row = package(parent_id) or {}
    try:
        obs = json.loads(row.get("observability_json") or "{}")
    except json.JSONDecodeError:
        obs = {}
    return str(obs.get("understood_objective") or request["objective"])


def _api_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "apps" / "api"


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{method} {url} HTTP {exc.code}: {detail}") from exc


def _wait_health(base: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _http_json("GET", base + "/health", timeout=3)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Campus API did not answer /health")


def _start_api() -> tuple:
    """The process Milo embeds: uvicorn app.main:app, then POST /milo/jobs.

    The API runs with apps/api as its working directory. A relative AYVEN_DB
    would then be a different file from the one this process reads.
    """
    db = os.environ.get("AYVEN_DB")
    if db:
        os.environ["AYVEN_DB"] = str(Path(db).resolve())
    port = int(os.environ.get("AYVEN_EVAL_PORT", "8877"))
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=_api_dir(),
        env=os.environ.copy(),
    )
    return proc, base


def _stop_api(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


_JOB_TERMINAL = {"COMPLETED", "FAILED", "UNRESOLVED", "AWAITING_APPROVAL", "REPAIRING"}


def _run_milo_job(base: str, request: dict) -> dict:
    """Submit through the installed Milo route and read the result Milo stores."""
    created = _http_json("POST", base + "/milo/jobs", {"objective": request["objective"], "title": request["title"]})
    project_id = created.get("project_id") or ""
    if not project_id:
        raise RuntimeError("POST /milo/jobs did not return a project_id")
    answered = False
    deadline = time.time() + float(os.environ.get("AYVEN_EVAL_JOB_BUDGET_S", "5400"))
    last: dict = {}
    while time.time() < deadline:
        last = _http_json("GET", base + f"/milo/jobs/{project_id}", timeout=60)
        state = last.get("workflow_state") or ""
        if state == "AWAITING_CLARIFICATION" and request.get("answer") and not answered:
            package_id = last.get("package_id") or ""
            if not package_id:
                raise RuntimeError("clarification was waiting and the job had no package_id")
            _http_json("POST", base + f"/work-packages/{package_id}/clarification", {"answer": request["answer"]})
            answered = True
            time.sleep(2)
            continue
        if state in _JOB_TERMINAL:
            return last
        if state == "AWAITING_CLARIFICATION" and not request.get("answer"):
            return last
        time.sleep(8)
    raise TimeoutError(f"milo job {project_id} stayed at {last.get('workflow_state') or last.get('status')}")


def run_pipeline(request: dict, out_dir: Path) -> dict:
    from app.intelligence.store import package

    from scripts.model_eval.score import call_timing, score_plan

    proc, base = _start_api()
    started = time.time()
    try:
        _wait_health(base)
        status = _run_milo_job(base, request)
    finally:
        _stop_api(proc)
    wall = round(time.time() - started, 1)
    project_id = status.get("project_id") or ""
    parent_id = status.get("package_id") or ""
    plan = status.get("result") or ""
    rows = _rows(project_id)
    evidence, pages = _evidence_text(parent_id)
    objective = _objective_for(request, parent_id)
    try:
        obs = json.loads((package(parent_id) or {}).get("observability_json") or "{}")
    except json.JSONDecodeError:
        obs = {}
    ids = [parent_id] + [item.get("id") or "" for item in rows]
    attempts = _attempts(ids)
    scored = score_plan(plan, objective, evidence, attempts)
    calls = _logged_calls(Path(os.environ.get("AYVEN_EVAL_CALL_LOG", out_dir / "calls.jsonl")))
    timing = call_timing(calls or attempts, attempts)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.md").write_text(plan or "No plan was stored.\n", encoding="utf-8")
    (out_dir / "evidence.json").write_text(json.dumps({
        "objective": objective,
        "pages": pages,
        "queries": obs.get("queries") or [],
        "failures": obs.get("failures") or [],
        "research_budget": obs.get("research_budget") or {},
        "research_gaps": obs.get("research_gaps") or [],
    }, indent=2), encoding="utf-8")
    (out_dir / "generations.json").write_text(json.dumps(attempts, indent=2), encoding="utf-8")
    result = {
        "id": request["id"],
        "status": "completed",
        "entry": "POST /milo/jobs",
        "workflow_state": status.get("workflow_state") or "",
        "wall_s": wall,
        "project_id": project_id,
        "package_id": parent_id,
        "objective": objective,
        "score": scored,
        "completion_tokens": timing["completion_tokens"],
        "prompt_tokens": timing["prompt_tokens"],
        "timing": timing,
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        (
            f"eval {request['id']} entry=POST /milo/jobs status=completed gate={scored['gate_pass']} "
            f"job_s={wall:.0f} load_s={timing['load_s']:.0f} "
            f"prompt_tps={timing['prompt_tokens_per_second']} gen_tps={timing['generation_tokens_per_second']} "
            f"calls={timing['model_calls']} retries={timing['retries']} timeouts={timing['timeouts']}"
        ),
        flush=True,
    )
    return result


def run_compact(evidence_path: Path, out_dir: Path) -> dict:
    """Same opened pages, compact evidence pack, section writer only."""
    from types import SimpleNamespace

    from app.intelligence.deliverable import _REQUIRED
    from app.intelligence.execution import _fill_plan_sections, _section_evidence, _section_jobs, _section_prompt

    from scripts.model_eval.score import score_plan

    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    programme = SimpleNamespace(
        objective=payload.get("objective") or "",
        parent_id="",
        research={"evidence": payload.get("pages") or []},
    )
    prompts = {}
    for mode in ("full", "compact"):
        os.environ["AYVEN_EVIDENCE_PACK"] = mode
        job = next(item for item in _section_jobs(programme.objective) if item[0] == "pricing")
        prompts[mode] = {
            "chars": len(_section_prompt(job[2], 1, "", _section_evidence(programme))),
            "evidence": _section_evidence(programme),
        }
    os.environ["AYVEN_EVIDENCE_PACK"] = "compact"
    sections = {key: "" for key in _REQUIRED}
    blob = "\n".join(
        f"{item.get('source_url') or ''}\n{item.get('source_title') or ''}\n{item.get('extracted_content') or ''}"
        for item in programme.research["evidence"]
    )
    started = time.time()
    rejected = _fill_plan_sections(programme, sections, blob)
    wall = round(time.time() - started, 1)
    from app.intelligence.execution import _assemble_plan

    plan = _assemble_plan(sections, programme.objective)
    scored = score_plan(plan, programme.objective, blob, [])
    scored["section_reasons_after_compact"] = rejected
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plan.md").write_text(plan, encoding="utf-8")
    result = {
        "id": "alba-compact",
        "status": "completed",
        "wall_s": wall,
        "prompt_comparison": prompts,
        "score": scored,
        "only_change": "AYVEN_EVIDENCE_PACK=compact on the Alba pages already opened. Model, num_ctx, temperature, and the gate were not changed.",
    }
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"eval alba-compact status=completed gate={scored['gate_pass']} seconds={wall:.0f}", flush=True)
    return result


def main() -> int:
    mode = os.environ.get("AYVEN_EVAL_MODE", "pipeline")
    out = Path(os.environ.get("AYVEN_EVAL_OUT", "eval-artifacts"))
    if mode == "compact":
        run_compact(Path(os.environ["AYVEN_EVAL_EVIDENCE"]), out)
        return 0
    from scripts.model_eval.objectives import REQUESTS

    request_id = os.environ["AYVEN_EVAL_REQUEST"]
    request = next(item for item in REQUESTS if item["id"] == request_id)
    run_pipeline(request, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
