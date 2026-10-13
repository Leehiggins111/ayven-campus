import os
from pathlib import Path

import pytest

os.environ["AYVEN_ENV"] = "local"
os.environ["AYVEN_LLM_STUB"] = "1"
os.environ["AYVEN_ALLOW_ESCALATION"] = "0"
os.environ["AYVEN_RESEARCH_MODE"] = "fixtures"
os.environ["AYVEN_USE_QWEN_AGENT"] = "0"
os.environ["AYVEN_RETRY_BACKOFF_S"] = "0"
os.environ.pop("AYVEN_LLM_API_KEY", None)
os.environ.pop("AYVEN_LOCAL_LLM_BASE_URL", None)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Each test gets its own database and temp directory."""
    path = tmp_path / "ayven.db"
    monkeypatch.setenv("AYVEN_DB", str(path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    from app.db import connect, reset_connection_state

    reset_connection_state()
    connection = connect()
    connection.close()
    yield path
    reset_connection_state()
