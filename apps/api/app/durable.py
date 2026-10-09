"""DBOS recovery with atomic, JSON-only application checkpoints.

The SQLite application database and DBOS system database must both live on a
persistent volume. Database mutations are replay-safe; external writes are not
part of this runtime's contract. One API process owns this SQLite worker.
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


def enabled() -> bool:
    return os.environ.get('AYVEN_DURABLE', '0') == '1'


def initialize() -> None:
    global _started, _projects
    with _lock:
        if _started:
            return
        conn = connect()
        conn.execute('CREATE TABLE IF NOT EXISTS durable_checkpoints (job_id TEXT NOT NULL, stage TEXT NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY(job_id,stage))')
        conn.commit(); conn.close()
        system_url = os.environ.get('AYVEN_WORKFLOW_DATABASE_URL') or f'sqlite:///{db_path().resolve().parent}/ayven-workflows.sqlite'
        DBOS(config={'name': 'ayven-engine', 'system_database_url': system_url,
                     'application_version': 'durable-v1', 'enable_otlp': False})
        DBOS.launch()
        _projects = DBOS.register_queue("ayven-projects", concurrency=1)
        _started = True
    # Recover API admission crashes (project committed before DBOS enqueue).
    conn = connect()
    rows = conn.execute("SELECT p.id FROM projects p WHERE p.status='running' AND (EXISTS(SELECT 1 FROM tasks t WHERE t.id='engine:'||p.id) OR NOT EXISTS(SELECT 1 FROM work_packages w WHERE w.project_id=p.id))").fetchall()
    conn.close()
    for row in rows:
        with SetWorkflowID(f"project:{row['id']}"):
            _projects.enqueue(project_workflow, row['id'])


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


def atomic_stage(job_id: str, stage: str, action):
    """Close the app-commit/DBOS-checkpoint crash window with a local receipt."""
    with stage_transaction() as conn:
        old = conn.execute('SELECT state_json FROM durable_checkpoints WHERE job_id=? AND stage=?', (job_id, stage)).fetchone()
        if old:
            return json.loads(old['state_json'])
        result = action()
        conn.execute('INSERT INTO durable_checkpoints VALUES(?,?,?)', (job_id, stage, json.dumps(result)))
    return result


@DBOS.step()
def prepare_project(project_id: str) -> dict:
    def action():
        from .intelligence.execution import Programme
        conn = connect()
        project = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if not project:
            raise KeyError('project not found')
        # Stable task identity also permits Milo/Campus to correlate a recovered job.
        task_id = f'engine:{project_id}'
        conn.execute('INSERT OR IGNORE INTO tasks(id,project_id,agent_id,title,brief,status,created_at) VALUES(?,?,?,?,?,?,?)',
                     (task_id, project_id, 'research-mgr', project['title'], project['objective'], 'running', project['created_at']))
        programme = Programme(project_id, project['objective'], task_id)
        programme.prepare_all()
        return snapshot(programme)
    return atomic_stage(project_id, 'prepare', action)


@DBOS.step()
def execute_role(project_id: str, role: str, state: dict) -> dict:
    def action():
        from .intelligence.execution import _complete
        programme = restore(state)
        for prompt in programme.prompts(role):
            text, _, meta = _complete(role, prompt['system'], prompt['user'],
                max_tokens=480 if role == 'MANAGER' else 320,
                programme=programme, package_id=prompt['id'])
            if meta.get('error'):
                raise RuntimeError('Workforce model call failed; no fixture fallback was used')
            programme.bind(role, prompt['id'], text, meta)
        return snapshot(programme)
    return atomic_stage(project_id, role, action)


@DBOS.step()
def finish_project(project_id: str) -> str:
    def action():
        from .orchestrator import finalize_project
        finalize_project(project_id)
        conn = connect()
        row = conn.execute('SELECT status FROM projects WHERE id=?', (project_id,)).fetchone()
        conn.execute('UPDATE tasks SET status=? WHERE id=?', (row['status'], f'engine:{project_id}'))
        return row['status']
    return atomic_stage(project_id, 'finish', action)


@DBOS.step()
def fail_project(project_id: str, error_type: str) -> None:
    def action():
        conn = connect()
        conn.execute('UPDATE projects SET status=?, result=? WHERE id=?',
                     ('rejected', f'Engine stopped: {error_type}. Review server logs before retrying.', project_id))
        conn.execute('UPDATE tasks SET status=? WHERE id=?', ('rejected', f'engine:{project_id}'))
        from .intelligence.workflow import log_transition
        from .distribution import update_package
        for row in conn.execute('SELECT id FROM work_packages WHERE project_id=? AND parent_id IS NULL', (project_id,)).fetchall():
            log_transition(row['id'], 'FAILED', f'Engine stopped: {error_type}')
            update_package(row['id'], status='rejected', campus_stage='FAILED', stage='exception')
        return None
    atomic_stage(project_id, 'failed', action)


@DBOS.workflow()
def project_workflow(project_id: str) -> str:
    try:
        state = prepare_project(project_id)
        if not state['programme']['paused']:
            for role in ('EMPLOYEE', 'SUPERVISOR', 'MANAGER'):
                state = execute_role(project_id, role, state)
        return finish_project(project_id)
    except Exception as exc:
        fail_project(project_id, type(exc).__name__)
        raise


def enqueue_project(project_id: str):
    initialize()
    with SetWorkflowID(f'project:{project_id}'):
        return _projects.enqueue(project_workflow, project_id)


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
