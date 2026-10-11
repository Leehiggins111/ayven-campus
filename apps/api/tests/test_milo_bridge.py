from fastapi.testclient import TestClient
from app.main import app
from app.db import connect, reset_connection_state


def test_milo_reads_completed_and_waiting_jobs_with_same_ids(tmp_path, monkeypatch):
    monkeypatch.setenv('AYVEN_DB',str(tmp_path/'bridge.sqlite'))
    reset_connection_state()
    conn=connect()
    conn.executemany('INSERT INTO projects VALUES(?,?,?,?,?,?)',[
        ('finished','Calculation','Calculate 6 * 7.','complete','42.00','2026-10-09'),
        ('waiting','Research','Find suppliers','waiting','Draft','2026-10-09')])
    conn.commit();conn.close()
    client=TestClient(app)
    projects=client.get('/api/v1/projects').json()['items']
    missions=client.get('/api/v1/missions').json()['items']
    assert {x['id'] for x in projects}=={x['projectId'] for x in missions}=={'finished','waiting'}
    assert client.get('/api/v1/missions/finished').json()['status']=='COMPLETED'
    waiting=client.get('/api/v1/projects/waiting').json()
    assert waiting['status']=='BLOCKED' and waiting['blockers']
    assert client.get('/api/v1/projects/missing').status_code==404
    reset_connection_state()
