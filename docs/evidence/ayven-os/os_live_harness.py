"""Live Ayven OS evidence against a REAL running Campus (uvicorn + on-disk SQLite).

Proves the Stage 3 Definition of Done:
  * a Milo-path create (Bearer owner + X-Ayven-Owner, exactly what Milo's proxy sends) persists in
    the platform's real agents table and appears in GET /state (the graphical Agent Workforce);
  * it survives a full Campus process restart;
  * it produces an audit row with the owner as actor;
  * GUI path (Bearer owner, no X-Ayven-Owner) and Milo path create consistent persisted state and
    consistent audit records;
  * guarded (busy -> 409, missing -> 404), rejected (unknown department -> 400) and unauthorised
    (read-only token -> 401) cases all behave and are audited, with nothing claimed as success.
"""
import json, os, subprocess, sqlite3, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ["CAMPUS_ROOT"]
PORT = os.environ.get("OS_PORT", "8799")
BASE = f"http://127.0.0.1:{PORT}"
DB = os.environ["AYVEN_DB"]
OWNER = os.environ["AYVEN_API_TOKEN"]
READ = os.environ["AYVEN_READ_TOKEN"]
ev = {"base": BASE, "steps": []}


def rec(name, **kw):
    ev["steps"].append({"step": name, **kw})
    print(name, {k: v for k, v in kw.items() if k != "body"})


def call(method, path, token=None, owner=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if owner:
        req.add_header("X-Ayven-Owner", owner)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def seed_db():
    sys.path[:0] = [ROOT, ROOT + "/apps/api"]
    from apps.api.app.db import connect, init_db, seed, reset_connection_state
    reset_connection_state()
    c = connect(); init_db(c); seed(c); c.close()
    reset_connection_state()


def launch():
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "os_server.py")],
                         env={**os.environ}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            s, _b = call("GET", "/os/actions", token=OWNER)
            if s == 200:
                return p
        except Exception:
            pass
        time.sleep(0.2)
    p.terminate()
    raise SystemExit("Campus did not come up")


def ids_in_state():
    s, b = call("GET", "/state", token=OWNER)
    return s, {a["id"] for a in b.get("agents", [])}


def main():
    seed_db()
    proc = launch()
    try:
        # 1. GUI path: Bearer owner, no X-Ayven-Owner -> actor is the owner principal.
        s, b = call("POST", "/os/actions/agent.create", token=OWNER,
                    body={"name": "GUI Agent", "role": "Ops lead", "department_id": "research"})
        gui_id = b.get("result", {}).get("agent", {}).get("id")
        rec("gui_create", status=s, id=gui_id, ok=b.get("ok"))

        # 2. Milo path: Bearer owner + X-Ayven-Owner (byte-identical to what Milo's proxy sends).
        s, b = call("POST", "/os/actions/agent.create", token=OWNER, owner="lee@ayven.local",
                    body={"name": "Milo Agent", "role": "Research analyst", "department_id": "research"})
        milo_id = b.get("result", {}).get("agent", {}).get("id")
        rec("milo_create", status=s, id=milo_id, ok=b.get("ok"), managed_by=b.get("result", {}).get("agent", {}).get("managed_by"))

        # 3. Both appear in the real agents table via a Milo read and via the GUI /state view.
        s, b = call("POST", "/os/actions/agent.list", token=OWNER, owner="lee@ayven.local", body={"department_id": "research"})
        listed = {a["id"] for a in b.get("result", {}).get("agents", [])}
        rec("milo_list_research", status=s, has_gui=gui_id in listed, has_milo=milo_id in listed)
        ss, state_ids = ids_in_state()
        rec("state_before_restart", status=ss, has_gui=gui_id in state_ids, has_milo=milo_id in state_ids)

        # 4. Rejected: unknown department -> 400, nothing created.
        s, b = call("POST", "/os/actions/agent.create", token=OWNER, owner="lee@ayven.local",
                    body={"name": "Nope", "role": "x", "department_id": "does-not-exist"})
        rec("rejected_unknown_department", status=s, detail=b.get("detail"))

        # 5. Unauthorised: read-only token cannot invoke (POST) at the access boundary.
        s, b = call("POST", "/os/actions/agent.list", token=READ, body={})
        rec("readonly_cannot_invoke", status=s)
        # And no auth at all.
        s, b = call("POST", "/os/actions/agent.list", body={})
        rec("unauthenticated_refused", status=s)

        # 6. Guarded retire: a busy agent cannot be retired (409); a missing one is 404; an idle one works.
        con = sqlite3.connect(DB, timeout=10)
        con.execute("UPDATE agents SET status='working', current_task_id='t-live' WHERE id=?", (milo_id,))
        con.commit(); con.close()
        s, b = call("POST", "/os/actions/agent.retire", token=OWNER, owner="lee@ayven.local", body={"id": milo_id})
        rec("retire_busy_blocked", status=s, detail=b.get("detail"))
        s, b = call("POST", "/os/actions/agent.retire", token=OWNER, owner="lee@ayven.local", body={"id": "ag-does-not-exist"})
        rec("retire_missing_404", status=s)
        con = sqlite3.connect(DB, timeout=10)
        con.execute("UPDATE agents SET status='idle', current_task_id=NULL WHERE id=?", (gui_id,))
        con.commit(); con.close()
        s, b = call("POST", "/os/actions/agent.retire", token=OWNER, body={"id": gui_id})
        rec("retire_idle_ok", status=s, new_status=b.get("result", {}).get("agent", {}).get("status"))

        # 7. Audit parity: GUI create actor vs Milo create actor; refused/bad_request recorded too.
        s, b = call("GET", "/os/audit", token=OWNER)
        entries = b.get("entries", [])
        by = {}
        for e in entries:
            by.setdefault(e["action"], []).append(e)
        creates = by.get("agent.create", [])
        gui_actor = next((e["actor"] for e in creates if e["outcome"] == "ok" and "GUI" in e.get("input_summary", "")), None)
        milo_actor = next((e["actor"] for e in creates if e["outcome"] == "ok" and "Milo" in e.get("input_summary", "")), None)
        outcomes = sorted({e["outcome"] for e in entries})
        rec("audit", total=len(entries), gui_actor=gui_actor, milo_actor=milo_actor, outcomes=outcomes)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    # 8. RESTART: a fresh Campus process on the same DB file. The agents and the audit trail persist.
    time.sleep(1)
    proc2 = launch()
    try:
        ss, state_ids = ids_in_state()
        rec("state_after_restart", status=ss, has_gui=gui_id in state_ids, has_milo=milo_id in state_ids)
        s, b = call("GET", "/os/audit", token=OWNER)
        rec("audit_after_restart", total=len(b.get("entries", [])))
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=10)
        except Exception:
            proc2.kill()

    ev["gui_id"] = gui_id
    ev["milo_id"] = milo_id
    out = os.path.join(HERE, "os_live_evidence.json")
    with open(out, "w") as f:
        json.dump(ev, f, indent=1)
    print("\nEVIDENCE ->", out)


if __name__ == "__main__":
    main()
