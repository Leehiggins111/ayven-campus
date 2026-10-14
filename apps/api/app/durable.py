"""DBOS recovery with atomic, JSON-only application checkpoints.

Application rows live in local SQLite or in remote libsql over HTTP (Hrana).
There is no embedded replica. DBOS system tables stay on local SQLite. Hosted
mode refuses to start when AYVEN_DB_URL or AYVEN_DB_AUTH_TOKEN is missing.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

from dbos import DBOS, SetWorkflowID
from .db import connect, db_path, stage_transaction

_started = False
_lock = threading.Lock()
STATE_VERSION = 1
_projects = None


def thread_stub_armed() -> bool:
    """The old in-process runner is a local test switch. It does not execute a job."""
    if os.environ.get("AYVEN_THREAD_STUB") != "1":
        return False
    if os.environ.get("AYVEN_ENV") != "local":
        raise RuntimeError("AYVEN_THREAD_STUB is a test switch and cannot run outside AYVEN_ENV=local")
    return True


def enabled() -> bool:
    """Durable mode is the only execution mode. The thread stub turns it off in tests."""
    if thread_stub_armed():
        return False
    return True


def _workflow_database_url() -> str:
    """DBOS system tables stay on local SQLite.

    A remote ``AYVEN_DB_URL`` is not a workflow database. DBOS does not speak
    libsql, and remote sync of these tables is unsupported.
    """
    explicit = (os.environ.get("AYVEN_WORKFLOW_DATABASE_URL") or "").strip()
    if explicit:
        return explicit
    url = (os.environ.get("AYVEN_DB_URL") or "").strip()
    if url.startswith("file:"):
        local = url[5:]
        if local.startswith("//"):
            local = local[2:]
        parent = Path(local).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{parent / 'ayven-workflows.sqlite'}"
    return f"sqlite:///{db_path().resolve().parent / 'ayven-workflows.sqlite'}"


def _maybe_crash(stage: str) -> None:
    """Test hook. ``AYVEN_CRASH_STAGE`` exits the process before that stage commits."""
    if os.environ.get("AYVEN_ENV") != "local":
        return
    wanted = (os.environ.get("AYVEN_CRASH_STAGE") or "").strip()
    if not wanted or wanted != stage:
        return
    barrier = (os.environ.get("AYVEN_CRASH_BARRIER") or "").strip()
    if barrier:
        Path(barrier).write_text(stage)
    os._exit(23)


def _pulse(project_id: str, owner: str, fence: int):
    """Extend the lease while a step runs. A lost lease is visible to the step."""
    stop = threading.Event()
    lost = threading.Event()
    if not owner or not int(fence or 0):
        return stop.set, lost
    from .intelligence.leases import heartbeat, lease_seconds

    def beat() -> None:
        interval = max(5, lease_seconds() // 3)
        while not stop.wait(interval):
            if not heartbeat(project_id, owner, int(fence)):
                lost.set()
                return

    heartbeat(project_id, owner, int(fence))
    threading.Thread(target=beat, name="ayven-lease", daemon=True).start()
    return stop.set, lost


def _hold(project_id: str, owner: str, fence: int):
    if not owner or not int(fence or 0):
        return None
    from .intelligence.leases import bind

    return bind(project_id, owner, int(fence))


def _drop(token) -> None:
    if token is None:
        return
    from .intelligence.leases import reset

    reset(token)


def initialize() -> None:
    global _started, _projects
    with _lock:
        if _started:
            return
        conn = connect()
        conn.execute('CREATE TABLE IF NOT EXISTS durable_checkpoints (job_id TEXT NOT NULL, stage TEXT NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY(job_id,stage))')
        conn.commit(); conn.close()
        system_url = _workflow_database_url()
        DBOS(config={'name': 'ayven-engine', 'system_database_url': system_url,
                     'application_version': 'durable-v1', 'enable_otlp': False})
        DBOS.launch()
        _projects = DBOS.register_queue("ayven-projects", concurrency=1)
        _started = True
    # Recover API admission crashes (project committed before DBOS enqueue).
    # DBOS.launch already resumes a workflow that exists. This loop only starts
    # a job that never got a workflow, and only after a claim.
    from .intelligence.leases import claim, expire_dead_owner

    conn = connect()
    rows = conn.execute("SELECT p.id FROM projects p WHERE p.status='running' AND (EXISTS(SELECT 1 FROM tasks t WHERE t.id='engine:'||p.id) OR NOT EXISTS(SELECT 1 FROM work_packages w WHERE w.project_id=p.id))").fetchall()
    conn.close()
    for row in rows:
        workflow_id = f"project:{row['id']}"
        try:
            DBOS.retrieve_workflow(workflow_id)
            continue
        except Exception:
            pass
        expire_dead_owner(row['id'])
        owner = f"dbos:{os.getpid()}"
        fence = claim(row['id'], owner)
        if fence is None:
            continue
        with SetWorkflowID(workflow_id):
            _projects.enqueue(project_workflow, row['id'], owner, int(fence))


def shutdown() -> None:
    global _started
    if _started:
        DBOS.destroy()
        _started = False


def snapshot(programme) -> dict:
    state = dict(programme.__dict__)
    if state.get('employee_session') is not None:
        raise ValueError('live employee sessions need an explicit durable serializer')
    state['skills'] = [{**asdict(skill), 'path': str(skill.path)} for skill in programme.skills]
    state['_supervisor_checked'] = sorted(programme._supervisor_checked)
    result = {'version': STATE_VERSION, 'programme': state}
    json.dumps(result)  # Never silently stringify an unsupported runtime object.
    return result


def restore(state: dict):
    from .intelligence.execution import Programme
    from .intelligence.skills import Skill
    if state.get('version') != STATE_VERSION:
        raise ValueError('unsupported durable state version')
    values = dict(state['programme'])
    values['skills'] = [Skill(**{**skill, 'path': Path(skill['path'])}) for skill in values['skills']]
    values['_supervisor_checked'] = set(values['_supervisor_checked'])
    programme = Programme.__new__(Programme)
    programme.__dict__.update(values)
    return programme


def saved_stage(job_id: str, stage: str):
    """Read a committed receipt. This does not open a write transaction."""
    conn = connect()
    try:
        row = conn.execute(
            'SELECT state_json FROM durable_checkpoints WHERE job_id=? AND stage=?',
            (job_id, stage),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if not row:
        return None
    return json.loads(row['state_json'])


def atomic_stage(job_id: str, stage: str, action):
    """Commit the stage receipt with the action's writes.

    The action may write rows. It must not call the model. Model calls happen
    before this function so the write lock is not held while the model runs.
    """
    with stage_transaction() as conn:
        old = conn.execute('SELECT state_json FROM durable_checkpoints WHERE job_id=? AND stage=?', (job_id, stage)).fetchone()
        if old:
            return json.loads(old['state_json'])
        result = action()
        conn.execute('INSERT INTO durable_checkpoints VALUES(?,?,?)', (job_id, stage, json.dumps(result)))
    return result


@DBOS.step()
def prepare_project(project_id: str, owner: str = "", fence: int = 0) -> dict:
    """Plan and research outside the write lock. The receipt is a short commit."""
    saved = saved_stage(project_id, 'prepare')
    if saved is not None:
        return saved
    token = _hold(project_id, owner, fence)
    stop, lost = _pulse(project_id, owner, fence)
    try:
        from .intelligence.execution import Programme
        from .intelligence.recovery import resume_parent

        conn = connect()
        project = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        conn.close()
        if not project:
            raise KeyError('project not found')
        task_id = f'engine:{project_id}'
        conn = connect()
        conn.execute('INSERT OR IGNORE INTO tasks(id,project_id,agent_id,title,brief,status,created_at) VALUES(?,?,?,?,?,?,?)',
                     (task_id, project_id, 'research-mgr', project['title'], project['objective'], 'running', project['created_at']))
        conn.commit()
        conn.close()
        programme = Programme(project_id, project['objective'], task_id)
        parent = resume_parent(project_id)
        if parent:
            programme.resume_id = parent
        programme.prepare_all()
        if lost.is_set():
            from .intelligence.run_token import StaleWrite
            raise StaleWrite('lease lost during prepare')
        snap = snapshot(programme)
    finally:
        stop()
        _drop(token)

    def action():
        return snap

    return atomic_stage(project_id, 'prepare', action)


@DBOS.step()
def execute_role(project_id: str, role: str, state: dict, owner: str = "", fence: int = 0) -> dict:
    """Call the model first. The write transaction starts only when the reply is stored."""
    saved = saved_stage(project_id, role)
    if saved is not None:
        return saved
    from .intelligence.execution import _complete

    token = _hold(project_id, owner, fence)
    stop, lost = _pulse(project_id, owner, fence)
    try:
        programme = restore(state)
        calls = []
        build_research = {}
        for prompt in programme.prompts(role):
            builder = _build_turn(programme.task_class) if role == 'EMPLOYEE' else None
            if builder is not None:
                # The same dispatch run_objective uses: a program or web build runs its
                # builder (spec, tests, code, sandbox or browser check), not the generic prompt.
                # A failed build is a valid FAILED deliverable, not a model-call failure.
                text, meta = builder(programme, prompt['id'])
                from .intelligence.execution import BUDGET_STOPS, BudgetExceeded
                if project_id in BUDGET_STOPS:
                    raise BudgetExceeded(BUDGET_STOPS[project_id])
                calls.append((prompt['id'], text, meta))
                build_research = dict(programme.research)
                continue
            limit = 480 if role == 'MANAGER' else (1400 if role == 'EMPLOYEE' and programme.plan.get('planning_source') == 'live_model' else 320)
            text, _, meta = _complete(
                role, prompt['system'], prompt['user'], max_tokens=limit,
                programme=programme, package_id=prompt['id'],
            )
            if meta.get('error'):
                cause = " ".join(str(meta.get('error')).split())[:120]
                raise RuntimeError(f'Workforce model call failed ({cause}); no fixture fallback was used')
            calls.append((prompt['id'], text, meta))
        # The delivery review and its repair call are model calls too. They run here,
        # before the stage write opens, never inside atomic_stage.
        precheck = programme.delivery_calls() if role == 'MANAGER' else None
        if lost.is_set():
            from .intelligence.run_token import StaleWrite
            raise StaleWrite('lease lost during the model call')
        _maybe_crash(role)
    finally:
        stop()
        _drop(token)

    def action():
        stored = restore(state)
        if build_research:
            # The builder recorded its execution evidence on the programme it ran on.
            stored.research.update(build_research)
        if precheck is not None:
            stored._delivery_precheck = precheck
        for package_id, text, meta in calls:
            stored.bind(role, package_id, text, meta)
        return snapshot(stored)

    return atomic_stage(project_id, role, action)


def _build_turn(task_class: str):
    """The builder run_objective uses for this task class, or None for a generic prompt."""
    from .intelligence import execution
    if task_class == 'software_build':
        return execution._software_turn
    if task_class in ('website_build', 'application_build'):
        return execution._web_turn
    return None


@DBOS.step()
def finish_project(project_id: str, owner: str = "", fence: int = 0) -> str:
    token = _hold(project_id, owner, fence)
    try:
        def action():
            from .orchestrator import finalize_project
            finalize_project(project_id)
            conn = connect()
            row = conn.execute('SELECT status FROM projects WHERE id=?', (project_id,)).fetchone()
            conn.execute('UPDATE tasks SET status=? WHERE id=?', (row['status'], f'engine:{project_id}'))
            return row['status']
        return atomic_stage(project_id, 'finish', action)
    finally:
        _drop(token)


@DBOS.step()
def release_lease(project_id: str, owner: str, fence: int, state: str) -> None:
    if not owner or not int(fence or 0):
        return
    from .intelligence.leases import release

    release(project_id, owner, int(fence), state)


def rejection_reason(stage: str, cause: str) -> str:
    """Stage, cause, and the next action. The exception class alone is not a reason."""
    clean = " ".join((cause or "unknown").split())
    if len(clean) > 300:
        clean = clean[:300].rstrip() + "..."
    where = (stage or "job").strip() or "job"
    return (
        f"Stopped during {where}. Cause: {clean}. "
        "Next: read the server log for this job, then retry it. Nothing was sent."
    )


@DBOS.step()
def fail_project(project_id: str, stage: str, cause: str, owner: str = "", fence: int = 0) -> None:
    token = _hold(project_id, owner, fence)
    try:
        return _fail_project(project_id, stage, cause)
    finally:
        _drop(token)


def _fail_project(project_id: str, stage: str, cause: str) -> None:
    reason = rejection_reason(stage, cause)
    from .intelligence.execution import BUDGET_STOPS
    BUDGET_STOPS.pop(project_id, None)  # a retried job with a raised cap starts clean

    def action():
        from .goals import add_blocker
        # The exact cause is the blocker. A failed step is never routed to approval.
        if cause.startswith("BudgetExceeded"):
            add_blocker(project_id, "cap", f"Stopped: {cause.split(': ', 1)[-1]}.",
                        "Raise this job's budget cap, then retry the job.")
        elif "timeout" in cause.lower():
            add_blocker(project_id, "timeout", reason, "Retry the job; if it times out again, simplify the brief or raise the time limit.")
        else:
            add_blocker(project_id, "error", reason, "Read the cause, fix it, then retry the job.")
        conn = connect()
        conn.execute('UPDATE projects SET status=?, result=? WHERE id=?',
                     ('rejected', reason, project_id))
        conn.execute('UPDATE tasks SET status=? WHERE id=?', ('rejected', f'engine:{project_id}'))
        from .intelligence.workflow import log_transition
        from .distribution import update_package
        for row in conn.execute('SELECT id FROM work_packages WHERE project_id=? AND parent_id IS NULL', (project_id,)).fetchall():
            log_transition(row['id'], 'FAILED', reason)
            update_package(row['id'], status='rejected', campus_stage='FAILED', stage='exception', error=reason)
        # Nothing was delivered, so each acceptance criterion is not_met, never an empty list.
        from .goals import evaluate_criteria
        evaluate_criteria(project_id, '')
        return None
    atomic_stage(project_id, 'failed', action)


@DBOS.workflow()
def project_workflow(project_id: str, owner: str = "", fence: int = 0) -> str:
    stage = "prepare"
    try:
        state = prepare_project(project_id, owner, int(fence or 0))
        if not state['programme']['paused']:
            for role in ('EMPLOYEE', 'SUPERVISOR', 'MANAGER'):
                stage = role
                state = execute_role(project_id, role, state, owner, int(fence or 0))
        stage = "finish"
        status = finish_project(project_id, owner, int(fence or 0))
    except Exception as exc:
        fail_project(project_id, stage, f"{type(exc).__name__}: {exc}", owner, int(fence or 0))
        release_lease(project_id, owner, int(fence or 0), 'failed')
        raise
    release_lease(project_id, owner, int(fence or 0), 'done')
    return status


def enqueue_project(project_id: str):
    initialize()
    from .intelligence.leases import claim

    owner = f"dbos:{os.getpid()}"
    fence = claim(project_id, owner)
    workflow_id = f'project:{project_id}'
    if fence is None:
        # Another owner holds the job, or the attempt cap is spent.
        # Return the workflow if one was already started. Do not start another.
        try:
            return DBOS.retrieve_workflow(workflow_id)
        except Exception:
            return None
    with SetWorkflowID(workflow_id):
        return _projects.enqueue(project_workflow, project_id, owner, int(fence))


@DBOS.step()
def approval_step(approval_id: str, decision: str) -> list[str]:
    def action():
        from .orchestrator import resolve_approval, finalize_project
        from .intelligence.execution import resume_packages_for_approval
        resolve_approval(approval_id, decision)
        states = resume_packages_for_approval(approval_id) if decision == 'approved' else []
        conn = connect()
        row = conn.execute('SELECT project_id FROM approvals WHERE id=?', (approval_id,)).fetchone()
        if row and row['project_id']:
            finalize_project(row['project_id'])
        return states
    return atomic_stage(f'approval:{approval_id}', decision, action)


@DBOS.workflow()
def approval_workflow(approval_id: str, decision: str) -> list[str]:
    return approval_step(approval_id, decision)


def resolve_approval(approval_id: str, decision: str) -> list[str]:
    initialize()
    # Include decision so a contradictory request is checked instead of replaying
    # the previous response. Its application transaction rejects that conflict.
    with SetWorkflowID(f'approval:{approval_id}:{decision}'):
        return approval_workflow(approval_id, decision)


@DBOS.step()
def clarification_step(package_id: str, answer: str) -> str:
    def action():
        from .intelligence.execution import answer_clarification
        from .orchestrator import finalize_project
        result = answer_clarification(package_id, answer)
        conn = connect()
        row = conn.execute('SELECT project_id FROM work_packages WHERE id=?', (package_id,)).fetchone()
        finalize_project(row['project_id'])
        return result
    import hashlib
    return atomic_stage(f'clarification:{package_id}', hashlib.sha256(answer.encode()).hexdigest(), action)


@DBOS.workflow()
def clarification_workflow(package_id: str, answer: str) -> str:
    return clarification_step(package_id, answer)


def answer_clarification(package_id: str, answer: str) -> str:
    import hashlib
    initialize()
    with SetWorkflowID(f'clarification:{package_id}:{hashlib.sha256(answer.encode()).hexdigest()}'):
        return clarification_workflow(package_id, answer)
