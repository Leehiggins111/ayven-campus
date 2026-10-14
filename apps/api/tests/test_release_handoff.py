from fastapi.testclient import TestClient
from app.main import app


def test_milo_and_projects_share_one_admission_key(monkeypatch):
    from app import orchestrator
    calls = []
    monkeypatch.setattr(orchestrator, 'run_project', calls.append)
    client = TestClient(app)
    headers = {'Idempotency-Key': 'release-shared-admission'}
    first = client.post('/milo/jobs', json={'objective': 'Calculate 6 * 7'}, headers=headers)
    second = client.post('/projects', json={'objective': 'Calculate 6 * 7'}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()['project_id'] == second.json()['project_id']
    assert second.json()['replayed'] is True
    assert len(calls) == 1
    assert client.post('/milo/jobs', json={'objective': 'Calculate 9 * 9'}, headers=headers).status_code == 409
    assert client.post('/milo/jobs?idempotency_key=another', json={'objective': 'Calculate 6 * 7'}, headers=headers).status_code == 400


def test_read_only_token_cannot_create_backup(monkeypatch):
    monkeypatch.setenv('AYVEN_API_TOKEN', 'release-admin-test-token')
    monkeypatch.setenv('AYVEN_READ_TOKEN', 'release-reader-test-token')
    client = TestClient(app)
    assert client.get('/state', headers={'Authorization': 'Bearer release-reader-test-token'}).status_code == 200
    assert client.get('/admin/backup', headers={'Authorization': 'Bearer release-reader-test-token'}).status_code == 403


def test_legacy_access_key_protects_the_integrated_api(monkeypatch):
    monkeypatch.delenv('AYVEN_API_TOKEN', raising=False)
    monkeypatch.setenv('AYVEN_ACCESS_KEY', 'release-launcher-test-token')
    client = TestClient(app)
    assert client.get('/state').status_code == 401
    assert client.get('/state', headers={'Authorization': 'Bearer release-launcher-test-token'}).status_code == 200
