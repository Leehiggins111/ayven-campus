import pytest
from app.models import _ollama_chat


def test_local_calls_disable_thinking_and_capture_timing(monkeypatch):
    def post(url, **kwargs):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert kwargs["json"]["think"] is False
        assert kwargs["json"]["stream"] is False
        assert kwargs["follow_redirects"] is False
        assert "headers" not in kwargs
        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {"done": True, "message": {"content": "Finished draft", "thinking": "private"}, "total_duration": 2000000000, "eval_count": 12, "prompt_eval_count": 30}
        return Response()
    monkeypatch.setattr("httpx.post", post)
    text, tokens, info = _ollama_chat("http://127.0.0.1:11434/v1", "qwen3:4b", "system", "request", 100)
    assert text == "Finished draft" and tokens == 42
    assert info["total_duration_s"] == 2


def test_ollama_cannot_call_cloud_or_remote_endpoint():
    for base, model in [("https://api.example.com/v1", "qwen3:4b"), ("http://127.0.0.1:11434/v1", "qwen-cloud")]:
        with pytest.raises(ValueError):
            _ollama_chat(base, model, "", "", 100)
