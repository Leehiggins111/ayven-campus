# Deploy Ayven Campus (persistent)

This app is one FastAPI process that serves both `/campus` and the live API (`/state`, `/projects`, `/events/stream`, approvals). It is **not** a static site and does not belong on Vercel/Netlify as the only host.

## Recommended host: Render (free web service)

1. Open https://render.com and create a free account (GitHub login is fine).
2. New → Web Service → connect `Leehiggins111/ayven-campus`.
3. Settings:
   - Root directory: `apps/api`
   - Runtime: Python
   - Build: `pip install -r requirements.txt`
   - Start: `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health check: `/health`
4. Environment:
   - `AYVEN_LLM_STUB=1`
   - `AYVEN_DB=./data/ayven-campus.db`
   - `PYTHONPATH=.`
5. Deploy. Campus URL will be `https://<service>.onrender.com/campus`.

### Free-tier limits (important)

- The service **sleeps after ~15 minutes idle**. First request after sleep can take 30–60s.
- The free filesystem is **ephemeral**. SQLite survives process sleep on the same instance, but a **redeploy wipes the DB** unless you add a paid persistent disk.
- No paid LLM is required while `AYVEN_LLM_STUB=1`.

Railway and Fly.io are equally valid (one long-running process + optional volume). They also require your account.

Do not use Cloudflare Quick Tunnels or localhost for Lee’s review URL.
