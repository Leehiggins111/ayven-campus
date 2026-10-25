"""Live proof that a REGISTERED agent performs GENUINE model-backed work as a worker.

Runs a real Campus (uvicorn + on-disk SQLite) wired to a real local model (Ollama, OpenAI-compat),
assigns a research_brief to the registered agent research-e1, and shows:
  * the task runs on the real backend (execution_mode REAL, the served model named),
  * a non-empty result and linked evidence,
  * the task + evidence survive a full Campus process restart,
  * an audit row ties the owner to the action,
  * an unauthorised assignment (compliance, no capability) is refused.
Nothing is simulated: if the model is down the task fails visibly instead.
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = f"http://127.0.0.1:{os.environ['OS_PORT']}"
OWNER = os.environ["AYVEN_API_TOKEN"]
ev = {"base": BASE, "model_config": {
    "AYVEN_LOCAL_LLM_BASE_URL": os.environ.get("AYVEN_LOCAL_LLM_BASE_URL"),
    "AYVEN_EMPLOYEE_MODEL": os.environ.get("AYVEN_EMPLOYEE_MODEL"),
    "AYVEN_LLM_STUB": os.environ.get("AYVEN_LLM_STUB")}, "steps": []}


def call(method, path, body=None, owner="lee@ayven.local"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + OWNER)
    if owner:
        req.add_header("X-Ayven-Owner", owner)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def launch():
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "os_server.py")], env={**os.environ},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if call("GET", "/os/actions")[0] == 200:
                return p
        except Exception:
            pass
        time.sleep(0.2)
    p.terminate(); raise SystemExit("Campus did not come up")


def rec(step, **kw):
    ev["steps"].append({"step": step, **kw}); print(step, {k: v for k, v in kw.items() if k != "result_preview"})


def seed():
    sys.path[:0] = [os.environ["CAMPUS_ROOT"], os.environ["CAMPUS_ROOT"] + "/apps/api"]
    from apps.api.app.db import connect, init_db, seed, reset_connection_state
    reset_connection_state(); c = connect(); init_db(c); seed(c); c.close(); reset_connection_state()


def main():
    seed()
    p = launch()
    try:
        s, b = call("POST", "/os/actions/agent.assign", {
            "agent_id": "research-e1", "kind": "research_brief",
            "objective": "In 3-4 sentences, explain what an API contract is and why it matters for integration."})
        task = b.get("result", {}).get("task", {})
        rec("assign_real", status=s, task_status=task.get("status"), mode=task.get("execution_mode"),
            model=task.get("model"), tokens=task.get("tokens"),
            result_len=len(task.get("result_text") or ""), result_preview=(task.get("result_text") or "")[:200])
        ev["task_id"] = task.get("id")
        ev["result_preview"] = (task.get("result_text") or "")[:400]
        ev["evidence"] = task.get("evidence")

        s, b = call("POST", "/os/actions/agent.assign", {
            "agent_id": "compliance", "kind": "research_brief", "objective": "x"})
        rec("unauthorised_refused", status=s, detail=b.get("detail"))

        s, b = call("POST", "/os/actions/task.get", {"id": ev["task_id"]})
        rec("inspect", status=s, has_evidence=bool(b.get("result", {}).get("task", {}).get("evidence")))

        s, b = call("GET", "/os/audit?action=agent.assign")
        rec("audit", total=len(b.get("entries", [])),
            actors=sorted({e["actor"] for e in b.get("entries", [])}),
            outcomes=sorted({e["outcome"] for e in b.get("entries", [])}))
    finally:
        p.terminate()
        try: p.wait(timeout=10)
        except Exception: p.kill()

    time.sleep(1)
    p2 = launch()
    try:
        s, b = call("POST", "/os/actions/task.get", {"id": ev["task_id"]})
        t = b.get("result", {}).get("task", {})
        rec("after_restart", status=s, task_status=t.get("status"), mode=t.get("execution_mode"),
            result_persisted=bool(t.get("result_text")))
    finally:
        p2.terminate()
        try: p2.wait(timeout=10)
        except Exception: p2.kill()

    out = os.path.join(HERE, "agent_live_evidence.json")
    json.dump(ev, open(out, "w"), indent=1)
    print("\nEVIDENCE ->", out)
    print("RESULT PREVIEW:\n", ev.get("result_preview", "")[:400])


if __name__ == "__main__":
    main()
