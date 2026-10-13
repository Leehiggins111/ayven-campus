"""Process-level recovery at both sides of the application commit boundary."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1]
RUNNER = r'''
import json, os, sys
from pathlib import Path
from dbos import DBOS
from app.db import connect
from app import durable
root=Path(os.environ['PROBE_ROOT'])
mode=sys.argv[1]
if mode=='crash':
    conn=connect()
    conn.execute('INSERT INTO projects(id,title,objective,status,result,created_at) VALUES(?,?,?,?,?,?)',('proof','Calculation','Calculate 6 * 7.','running',None,'2026-10-09'))
    conn.commit(); conn.close()
    if os.environ['CRASH_POINT']=='admission':
        os._exit(23)
    real=durable.atomic_stage
    def crash(job,stage,action):
        if stage=='EMPLOYEE':
            if os.environ['CRASH_POINT']=='inside':
                def interrupted():
                    action()
                    os._exit(23)
                return real(job,stage,interrupted)
            result=real(job,stage,action)
            os._exit(23)
        return real(job,stage,action)
    durable.atomic_stage=crash
    durable.enqueue_project('proof').get_result()
else:
    durable.initialize()
    status=DBOS.retrieve_workflow('project:proof').get_result()
    # A repeated enqueue must retrieve the same work, without executing again.
    assert durable.enqueue_project('proof').get_result()==status
    conn=connect()
    project=dict(conn.execute('SELECT * FROM projects WHERE id="proof"').fetchone())
    parents=[dict(x) for x in conn.execute('SELECT * FROM work_packages WHERE project_id="proof" AND tier="MANAGER"')]
    calls=[dict(x) for x in conn.execute('SELECT * FROM model_calls')]
    checkpoints=[dict(x) for x in conn.execute('SELECT stage FROM durable_checkpoints WHERE job_id="proof"')]
    lease=conn.execute('SELECT state FROM job_leases WHERE job_id="proof"').fetchone()
    conn.close()
    (root/'proof.json').write_text(json.dumps({'status':status,'project':project,'parents':parents,'calls':calls,'checkpoints':checkpoints,'lease':dict(lease)['state'] if lease else ''}))
    durable.shutdown()
'''

@pytest.mark.parametrize('crash_point', ['admission', 'inside', 'after_commit'])
def test_real_project_recovers_without_duplicate_writes(tmp_path, crash_point):
    env={**os.environ,'PYTHONPATH':str(API),'AYVEN_DURABLE':'1','AYVEN_DB':str(tmp_path/'app.sqlite'),
         'AYVEN_LLM_STUB':'1','AYVEN_RESEARCH_MODE':'fixtures','AYVEN_USE_QWEN_AGENT':'0',
         'AYVEN_ALLOW_ESCALATION':'0','PROBE_ROOT':str(tmp_path),'CRASH_POINT':crash_point}
    for key in ('AYVEN_LOCAL_LLM_BASE_URL','AYVEN_LLM_API_KEY','AYVEN_WORKFLOW_DATABASE_URL'):
        env.pop(key,None)
    crash=subprocess.run([sys.executable,'-c',RUNNER,'crash'],env=env,capture_output=True,text=True,timeout=60)
    assert crash.returncode==23, crash.stderr
    resume=subprocess.run([sys.executable,'-c',RUNNER,'resume'],env=env,capture_output=True,text=True,timeout=60)
    assert resume.returncode==0, resume.stderr
    proof=json.loads((tmp_path/'proof.json').read_text())
    assert proof['status']=='complete'
    assert proof['project']['status']=='complete'
    assert '42' in proof['project']['result']
    assert len(proof['parents'])==1
    assert proof['parents'][0]['workflow_state']=='COMPLETED'
    assert len(proof['calls'])==3  # One employee, supervisor and manager invocation persisted.
    assert {x['stage'] for x in proof['checkpoints']}=={'prepare','EMPLOYEE','SUPERVISOR','MANAGER','finish'}
    assert proof['lease']=='done'


def test_a_model_call_does_not_hold_the_write_lock(tmp_path, monkeypatch):
    """A second connection can write while the role model call is in progress."""
    import sqlite3
    import threading

    from app.db import connect, reset_connection_state
    from app.models import set_role_generator
    from app import durable

    path = tmp_path / "lock.sqlite"
    monkeypatch.setenv("AYVEN_DB", str(path))
    monkeypatch.setenv("AYVEN_DURABLE", "1")
    monkeypatch.setenv("AYVEN_LLM_STUB", "1")
    monkeypatch.setenv("AYVEN_RESEARCH_MODE", "fixtures")
    monkeypatch.setenv("AYVEN_ALLOW_ESCALATION", "0")
    monkeypatch.delenv("AYVEN_WORKFLOW_DATABASE_URL", raising=False)
    monkeypatch.delenv("AYVEN_DB_URL", raising=False)
    reset_connection_state()
    conn = connect()
    conn.execute(
        "INSERT INTO projects(id,title,objective,status,result,created_at) VALUES(?,?,?,?,?,?)",
        ("lock-job", "Lock", "Calculate 6 * 7.", "running", None, "2026-10-13"),
    )
    conn.commit()
    conn.close()
    wrote = {"ok": False, "error": ""}

    def generator(role, system, user, max_tokens):
        def writer():
            try:
                other = sqlite3.connect(path, timeout=1)
                other.execute("UPDATE projects SET result=? WHERE id=?", ("during-call", "lock-job"))
                other.commit()
                other.close()
                wrote["ok"] = True
            except Exception as exc:
                wrote["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(3)
        return "42", 1, {"backend": "stub"}

    set_role_generator(generator)
    try:
        durable.enqueue_project("lock-job").get_result()
    finally:
        set_role_generator(None)
        durable.shutdown()
        reset_connection_state()
    assert wrote["ok"], wrote["error"]


def test_approval_is_atomic_idempotent_and_conflicts_are_rejected(tmp_path, monkeypatch):
    from app.db import connect, reset_connection_state
    from app.durable import atomic_stage, approval_step
    monkeypatch.setenv('AYVEN_DB', str(tmp_path/'approval.sqlite'))
    reset_connection_state()
    conn=connect()
    conn.execute('CREATE TABLE durable_checkpoints(job_id TEXT,stage TEXT,state_json TEXT,PRIMARY KEY(job_id,stage))')
    conn.execute('INSERT INTO projects(id,title,objective,status,result,created_at) VALUES(?,?,?,?,?,?)',('approval-project','Approval','Draft only','waiting',None,'2026-10-09'))
    conn.execute('INSERT INTO work_packages(id,project_id,title,objective,origin,stage,status,created_at,updated_at,tier,workflow_state,findings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('parent','approval-project','Draft','Draft only','milo','approval','needs_approval','now','now','MANAGER','AWAITING_APPROVAL','Verified draft'))
    conn.execute('INSERT INTO approvals(id,task_id,project_id,agent_id,summary,status,created_at) VALUES(?,?,?,?,?,?,?)',('approval','parent','approval-project','research-mgr','Review draft','pending','now'))
    conn.commit();conn.close()
    # Call undecorated application action; DBOS recovery is exercised separately above.
    fn=approval_step.__wrapped__
    assert fn('approval','approved')==['COMPLETED']
    assert fn('approval','approved')==['COMPLETED']
    with pytest.raises(ValueError,match='another decision'):
        fn('approval','rejected')
    conn=connect()
    assert conn.execute('SELECT status FROM projects WHERE id="approval-project"').fetchone()['status']=='complete'
    assert conn.execute('SELECT status FROM approvals').fetchone()['status']=='approved'
    conn.close()
    reset_connection_state()


def test_api_submission_to_milo_readback(tmp_path):
    script=r'''
import time
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    auth={'Authorization':'Bearer private-api-key-with-at-least-32-characters','Idempotency-Key':'same-objective'}
    first=client.post('/projects',json={'objective':'Calculate 6 * 7.'},headers=auth)
    assert first.status_code==200, first.text
    project_id=first.json()['project_id']
    second=client.post('/projects',json={'objective':'Calculate 6 * 7.'},headers=auth)
    assert second.json()['project_id']==project_id and second.json()['replayed']
    assert client.post('/projects',json={'objective':'Calculate 2 * 8.'},headers=auth).status_code==409
    for _ in range(100):
        project=client.get('/api/v1/projects/'+project_id,headers=auth).json()
        if project['status']=='COMPLETED': break
        time.sleep(.1)
    assert project['status']=='COMPLETED', project
    assert '42' in project['result']
    mission=client.get('/api/v1/missions/'+project_id,headers={'Authorization':'Bearer milo-read-only'}).json()
    assert mission['projectId']==project_id and mission['status']=='COMPLETED'
    assert client.post('/projects',json={'objective':'Calculate 1+1'},headers={'Authorization':'Bearer milo-read-only'}).status_code==401
'''
    env={**os.environ,'PYTHONPATH':str(API),'AYVEN_DURABLE':'1','AYVEN_DB':str(tmp_path/'api.sqlite'),
         'AYVEN_LLM_STUB':'1','AYVEN_RESEARCH_MODE':'fixtures','AYVEN_USE_QWEN_AGENT':'0',
         'AYVEN_ALLOW_ESCALATION':'0','AYVEN_API_TOKEN':'private-api-key-with-at-least-32-characters',
         'AYVEN_READ_TOKEN':'milo-read-only'}
    env.pop('AYVEN_WORKFLOW_DATABASE_URL',None)
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_unconfigured_live_model_is_visible_as_failure(tmp_path):
    script=r'''
from app.db import connect
from app import durable
conn=connect()
conn.execute('INSERT INTO projects(id,title,objective,status,result,created_at) VALUES(?,?,?,?,?,?)',('live-failure','Failure','Calculate 6 * 7.','running',None,'2026-10-09'))
conn.commit();conn.close()
try:
    durable.enqueue_project('live-failure').get_result()
except RuntimeError:
    pass
else:
    raise AssertionError('Unconfigured live model must fail')
conn=connect()
assert conn.execute('SELECT status FROM projects WHERE id="live-failure"').fetchone()['status']=='rejected'
assert conn.execute('SELECT workflow_state FROM work_packages WHERE tier="MANAGER"').fetchone()['workflow_state']=='FAILED'
assert conn.execute('SELECT state FROM job_leases WHERE job_id="live-failure"').fetchone()['state']=='failed'
conn.close()
durable.shutdown()
'''
    env={**os.environ,'PYTHONPATH':str(API),'AYVEN_DURABLE':'1','AYVEN_DB':str(tmp_path/'failed.sqlite'),
         'AYVEN_LLM_STUB':'0','AYVEN_RESEARCH_MODE':'fixtures','AYVEN_USE_QWEN_AGENT':'0','AYVEN_ALLOW_ESCALATION':'0'}
    for key in ('AYVEN_LOCAL_LLM_BASE_URL','AYVEN_WORKFLOW_DATABASE_URL'):
        env.pop(key,None)
    result=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr


def test_a_barrier_kills_the_process_mid_stage_and_the_restart_finishes(tmp_path):
    """The crash hook exits before the stage receipt. The next process finishes that stage once."""
    barrier = tmp_path / "barrier"
    env = {
        **os.environ,
        "PYTHONPATH": str(API),
        "AYVEN_DURABLE": "1",
        "AYVEN_ENV": "local",
        "AYVEN_DB": str(tmp_path / "barrier.sqlite"),
        "AYVEN_LLM_STUB": "1",
        "AYVEN_RESEARCH_MODE": "fixtures",
        "AYVEN_USE_QWEN_AGENT": "0",
        "AYVEN_ALLOW_ESCALATION": "0",
        "AYVEN_CRASH_STAGE": "EMPLOYEE",
        "AYVEN_CRASH_BARRIER": str(barrier),
    }
    for key in ("AYVEN_LOCAL_LLM_BASE_URL", "AYVEN_LLM_API_KEY", "AYVEN_WORKFLOW_DATABASE_URL", "AYVEN_THREAD_STUB"):
        env.pop(key, None)
    crash_script = r"""
from app.db import connect
from app import durable
conn = connect()
conn.execute(
    "INSERT INTO projects(id,title,objective,status,result,created_at) VALUES(?,?,?,?,?,?)",
    ("barrier-job", "Calculation", "Calculate 6 * 7.", "running", None, "2026-10-13"),
)
conn.commit()
conn.close()
durable.enqueue_project("barrier-job").get_result()
"""
    crash = subprocess.run([sys.executable, "-c", crash_script], env=env, capture_output=True, text=True, timeout=60)
    assert crash.returncode == 23, crash.stderr
    assert barrier.read_text() == "EMPLOYEE"
    resume_env = dict(env)
    resume_env.pop("AYVEN_CRASH_STAGE")
    resume_env.pop("AYVEN_CRASH_BARRIER")
    resume_script = r"""
from app.db import connect
from app import durable
durable.initialize()
from dbos import DBOS
status = DBOS.retrieve_workflow("project:barrier-job").get_result()
assert status == "complete", status
conn = connect()
calls = conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0]
stages = [row[0] for row in conn.execute("SELECT stage FROM durable_checkpoints WHERE job_id='barrier-job'")]
lease = conn.execute("SELECT state FROM job_leases WHERE job_id='barrier-job'").fetchone()[0]
conn.close()
assert calls == 3, calls
assert set(stages) == {"prepare", "EMPLOYEE", "SUPERVISOR", "MANAGER", "finish"}
assert lease == "done"
durable.shutdown()
print("resumed")
"""
    resume = subprocess.run([sys.executable, "-c", resume_script], env=resume_env, capture_output=True, text=True, timeout=60)
    assert resume.returncode == 0, resume.stderr
    assert "resumed" in resume.stdout
