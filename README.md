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
PYTHONPATH=. AYVEN_LLM_STUB=1 AYVEN_RESEARCH_MODE=fixtures AYVEN_ALLOW_ESCALATION=0 pytest -q
```

v1.0.0 adds the intelligence engine under `apps/api/app/intelligence/` and skills under `skills/`. Local tests use fixture pages and stub models. They do not prove Qwen quality. GPU validation is `./scripts/run_ayven_validation.sh` on a pod you already started. See `docs/VALIDATION.md`.

See `DEPLOY.md` for Render. This revision does not deploy.
