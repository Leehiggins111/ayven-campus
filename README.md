# Ayven Campus

Living HQ over a real event API.

## Clone and run

```bash
git clone https://github.com/Leehiggins111/ayven-campus.git
cd ayven-campus/apps/api
pip install -r requirements.txt
AYVEN_DB=/tmp/ayven-campus.db AYVEN_LLM_STUB=1 PYTHONPATH=. python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `/campus` on that process.

## Tests

```bash
cd apps/api
PYTHONPATH=. AYVEN_LLM_STUB=1 AYVEN_DB=/tmp/ayven-test.db pytest -q
```

See `DEPLOY.md` for Render.
