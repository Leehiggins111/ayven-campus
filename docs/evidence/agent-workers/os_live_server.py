import os, sys
ROOT = os.environ["CAMPUS_ROOT"]
sys.path[:0] = [ROOT, ROOT + "/apps/api"]
import uvicorn
from apps.api.app.main import app
uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("OS_PORT", "8799")), log_level="warning")
