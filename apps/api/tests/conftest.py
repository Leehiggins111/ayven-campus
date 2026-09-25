import os
from pathlib import Path

os.environ["AYVEN_DB"] = "/tmp/ayven-pytest-intel.db"
os.environ["AYVEN_LLM_STUB"] = "1"
os.environ["AYVEN_ALLOW_ESCALATION"] = "0"
os.environ["AYVEN_RESEARCH_MODE"] = "fixtures"
os.environ["AYVEN_USE_QWEN_AGENT"] = "0"
os.environ.pop("AYVEN_LLM_API_KEY", None)
os.environ.pop("AYVEN_LOCAL_LLM_BASE_URL", None)

db = Path("/tmp/ayven-pytest-intel.db")
if db.exists():
    db.unlink()
