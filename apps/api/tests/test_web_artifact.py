"""Exercise generated-artifact boundaries with real browser behavior, not phrase checks."""
import copy
import json
import pytest
from app.intelligence.web_artifact import (build_web_artifact, verified_web, artifact_files,
    validate_plan, run_browser, format_report)
from app.intelligence.planner import classify


FILES = {
    "index.html": '''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="style.css"></head><body><h1>Local job tracker</h1><form id="form"><label>Customer<input id="customer" required maxlength="80"></label><button id="save">Save job</button></form><p id="status"></p><ul id="jobs"></ul><script src="app.js"></script></body></html>''',
    "style.css": 'body{font:16px sans-serif;margin:20px;max-width:700px}input{max-width:90%;display:block}button{margin:8px 0}li{overflow-wrap:anywhere}',
    "app.js": '''let jobs=[];try{jobs=JSON.parse(localStorage.getItem('jobs')||'[]')}catch(e){jobs=[]}let editing=-1;const input=document.querySelector('#customer');function render(){const list=document.querySelector('#jobs');list.replaceChildren();jobs.forEach((name,i)=>{const li=document.createElement('li');li.textContent=name;const b=document.createElement('button');b.textContent='Edit';b.className='edit';b.onclick=()=>{editing=i;input.value=name};li.append(b);list.append(li)})}document.querySelector('#form').onsubmit=e=>{e.preventDefault();const name=input.value.trim();if(!name){document.querySelector('#status').textContent='Customer required';return}if(editing<0)jobs.push(name);else jobs[editing]=name;editing=-1;localStorage.setItem('jobs',JSON.stringify(jobs));render();input.value='';document.querySelector('#status').textContent='Saved'};render();'''
}


def step(action, selector='', value=''):
    return dict(action=action, selector=selector, value=value)


PLAN = dict(title='Local job tracker', scope='offline-browser', limitations=['Single browser profile; no shared accounts'], journeys=[
    dict(requirement='Create a job with the supplied customer name', category='create', steps=[step('fill','#customer','Ada'),step('click','#save'),step('text','#jobs','Ada')]),
    dict(requirement='Edit an existing customer and show the revised value', category='edit', steps=[step('click','.edit'),step('fill','#customer','Grace'),step('click','#save'),step('text','#jobs','Grace')]),
    dict(requirement='Reopen the application and retain the saved customer', category='persistence', steps=[step('fill','#customer','Lin'),step('click','#save'),step('reload'),step('text','#jobs','Lin')]),
    dict(requirement='Reject an empty customer instead of creating a job', category='validation', steps=[step('fill','#customer',''),step('click','#save'),step('invalid','#customer')]),
])


def test_specific_build_requests_precede_business_and_joinery_routes():
    assert classify('Build an internal door quotation application with saved jobs') == 'application_build'
    assert classify('Create a website for a coffee business with navigation and a calculator') == 'website_build'
    assert classify('Research coffee machine businesses') == 'business_research'


def test_plan_rejects_missing_persistence_edit_and_assertions():
    assert validate_plan(PLAN,'application_build')['scope'] == 'offline-browser'
    missing = copy.deepcopy(PLAN)
    missing['journeys'] = [j for j in missing['journeys'] if j['category'] != 'edit']
    with pytest.raises(ValueError,match='edit'):
        validate_plan(missing,'application_build')
    missing = copy.deepcopy(PLAN)
    missing['journeys'][2]['steps'] = [step('fill','#customer','Lin'),step('click','#save'),step('text','#jobs','Lin')]
    with pytest.raises(ValueError,match='reopening'):
        validate_plan(missing,'application_build')
    missing = copy.deepcopy(PLAN)
    missing['journeys'][2]['steps'] = [step('reload'),step('fill','#customer','Lin'),step('value','#customer','Lin')]
    with pytest.raises(ValueError,match='before reopening'):
        validate_plan(missing,'application_build')


@pytest.fixture(scope='module')
def built():
    import os
    previous=os.environ.get('AYVEN_ALLOW_CODE')
    os.environ['AYVEN_ALLOW_CODE']='1'
    calls=[]
    def complete(system,user,limit,ollama_format=None):
        calls.append(system)
        if system.startswith('Specify'): reply=PLAN
        elif system.startswith('Independently'): reply={'passed':True,'missing':[]}
        else:
            name=next(name for name in FILES if system.startswith('Write '+name+' only'))
            reply={name:FILES[name]}
        return json.dumps(reply),30,{'backend':'test-generator','model':'fixture'}
    result=build_web_artifact('Build a local customer job tracker with create, edit, reopen and input validation.', 'application_build',complete)
    if previous is None: os.environ.pop('AYVEN_ALLOW_CODE',None)
    else: os.environ['AYVEN_ALLOW_CODE']=previous
    return result,calls


def test_browser_checks_creation_edit_reopen_validation_and_script_mutation(built):
    (report,meta),calls=built
    assert meta['tests_passed'] is True, meta['web_execution']
    assert verified_web(report,meta)
    assert len(meta['web_execution']['checks']) == 8
    assert meta['web_execution']['mutation_rejected'] is True
    assert artifact_files(report) == FILES
    assert calls[0].startswith('Specify') and calls[-1].startswith('Independently')


def test_completion_rejects_source_tampering_and_missing_browser_evidence(built):
    (report,meta),_=built
    assert not verified_web(report.replace('localStorage','sessionStorage'),meta)
    broken=copy.deepcopy(meta)
    broken['web_execution']['checks'].pop()
    assert not verified_web(report,broken)
    broken=copy.deepcopy(meta)
    broken['web_execution']['screenshots'][0]['png_base64']='AAAA'
    assert not verified_web(report,broken)
    broken=copy.deepcopy(meta)
    broken['web_execution']['brief_review_passed']=False
    assert not verified_web(report,broken)
    assert not verified_web('Tests: PASSED',meta)


def test_failed_browser_keeps_draft_but_never_passes(monkeypatch):
    monkeypatch.setenv('AYVEN_ALLOW_CODE','0')
    result=run_browser(FILES,PLAN)
    report=format_report(FILES,PLAN,result)
    assert artifact_files(report) == FILES
    assert not verified_web(report,{'web_execution':result})
    assert result['status'] == 'disabled'


def test_engine_download_and_approval_share_the_verified_artifact(monkeypatch, built):
    import uuid
    import io
    import zipfile
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import connect
    from app.models import set_role_generator
    from app.intelligence.execution import run_objective, resume_approved_package
    from app.intelligence.workflow import log_transition
    from app.intelligence.web_preview import close_previews
    import app.intelligence.web_artifact as module
    (report, meta), _ = built
    monkeypatch.setattr(module,'build_web_artifact',lambda *_args: (report,meta))
    monkeypatch.setenv('AYVEN_LLM_STUB','0')
    monkeypatch.setenv('AYVEN_LOCAL_LLM_BASE_URL','http://127.0.0.1:11434/v1')
    project_id=str(uuid.uuid4())
    objective='Build a customer job tracking application with create, edit, persistence and validation.'
    conn=connect()
    conn.execute('INSERT INTO projects(id,title,objective,status,created_at) VALUES(?,?,?,?,?)',(project_id,'App',objective,'running','2026-10-11T00:00:00Z'))
    conn.commit(); conn.close()
    set_role_generator(lambda role,system,user,max_tokens: ('SYNTHESISE\nNothing was sent.' if role=='MANAGER' else 'ACCEPT\nNothing was sent.',2,{'backend':'generator'}))
    try:
        parent=run_objective(project_id,objective)
    finally:
        set_role_generator(None)
    conn=connect()
    row=dict(conn.execute('SELECT * FROM work_packages WHERE id=?',(parent,)).fetchone())
    conn.close()
    assert row['task_class']=='application_build'
    assert row['workflow_state']=='AWAITING_APPROVAL', row['findings']
    client=TestClient(app)
    zip_response=client.get(f'/work-packages/{parent}/artifact.zip')
    assert zip_response.status_code==200,zip_response.text[:500]
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        assert archive.read('app.js').decode()==FILES['app.js']
        assert archive.read('browser-390px.png').startswith(b'\x89PNG')
    assert client.get(f'/milo/jobs/{project_id}').json()['web_artifact_verified'] is True
    url=client.get(f'/work-packages/{parent}/preview').json()['url']
    assert url.startswith('http://127.0.0.1:')
    assert client.get(f'/work-packages/{parent}/preview').json()['url']==url
    from playwright.sync_api import sync_playwright
    from app.intelligence.web_browser_runner import chrome_path
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=chrome_path())
        page=browser.new_page()
        page.goto(url)
        frame=page.frame_locator('iframe')
        frame.locator('#customer').fill('Persisted preview user')
        frame.locator('#save').click()
        assert 'Persisted preview user' in frame.locator('#jobs').inner_text()
        page.reload()
        assert 'Persisted preview user' in frame.locator('#jobs').inner_text()
        assert 'window.miloDesktop' not in page.content()
        browser.close()
    close_previews()
    log_transition(parent,'APPROVED','Test approval')
    assert resume_approved_package(parent)=='COMPLETED'
    conn=connect()
    obs=json.loads(row['observability_json']); obs['web_execution']['passed']=False
    conn.execute('UPDATE work_packages SET observability_json=? WHERE id=?',(json.dumps(obs),parent))
    conn.commit();conn.close()
    assert client.get(f'/work-packages/{parent}/artifact.zip').status_code==409
    log_transition(parent,'APPROVED','Recheck invalidated evidence')
    assert resume_approved_package(parent)=='UNRESOLVED'
