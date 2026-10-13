from fastapi.testclient import TestClient
from app.main import app


def test_private_api_and_http_only_browser_session(monkeypatch):
    monkeypatch.setenv('AYVEN_API_TOKEN', 'a-private-test-key-with-at-least-32-characters')
    client=TestClient(app)
    assert client.get('/state').status_code==401
    assert client.get('/health').status_code==200
    monkeypatch.setenv('AYVEN_READ_TOKEN','milo-read-only')
    assert client.get('/api/v1/projects',headers={'Authorization':'Bearer milo-read-only'}).status_code==200
    assert client.post('/projects',json={'objective':'Calculate 6 * 7.'},headers={'Authorization':'Bearer milo-read-only'}).status_code==401
    assert client.post('/session',data={'key':'wrong'}).status_code==401
    assert client.get('/state',headers={'Authorization':'Bearer a-private-test-key-with-at-least-32-characters'}).status_code==200
    response=client.post('/session',data={'key':'a-private-test-key-with-at-least-32-characters'},follow_redirects=False)
    assert response.status_code==303
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert client.get('/state').status_code==200
    monkeypatch.setenv('AYVEN_API_TOKEN','rotated-private-test-key-with-at-least-32-characters')
    assert client.get('/state').status_code==401


def test_public_mode_cannot_start_without_private_access(monkeypatch):
    import pytest
    from app.access import validate_public_configuration
    monkeypatch.setenv('AYVEN_PUBLIC_MODE','1')
    monkeypatch.delenv('AYVEN_API_TOKEN',raising=False)
    with pytest.raises(RuntimeError,match='API_TOKEN'):
        validate_public_configuration()
