"""Shared private-deployment boundary for Campus and Milo API clients."""
import hashlib
import hmac
import os
import time
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = 'ayven_session'
TTL = 12 * 60 * 60


def token() -> str:
    return os.environ.get('AYVEN_API_TOKEN') or os.environ.get('AYVEN_ACCESS_KEY', '')


def session_value(now: int) -> str:
    stamp = str(now)
    digest = hmac.new(token().encode(), stamp.encode(), hashlib.sha256).hexdigest()
    return f'{stamp}.{digest}'


def authorized(request: Request) -> bool:
    key = token()
    if not key:
        return True  # Local development; public deployment fails startup without a key.
    supplied = request.headers.get('authorization', '')
    read_key = os.environ.get('AYVEN_READ_TOKEN', '')
    if read_key and request.method == 'GET' and not request.url.path.startswith('/admin/') and supplied.startswith('Bearer ') and hmac.compare_digest(supplied[7:].encode(), read_key.encode()):
        return True
    if supplied.startswith('Bearer ') and hmac.compare_digest(supplied[7:].encode(), key.encode()):
        return True
    cookie = request.cookies.get(COOKIE, '')
    try:
        stamp = int(cookie.split('.')[0])
        return 0 <= time.time() - stamp <= TTL and hmac.compare_digest(cookie.encode(), session_value(stamp).encode())
    except (ValueError, IndexError):
        return False


async def boundary(request: Request, call_next):
    if request.url.path in ('/health', '/login', '/session') or authorized(request):
        return await call_next(request)
    if request.method == 'GET' and 'text/html' in request.headers.get('accept', ''):
        return RedirectResponse('/login', status_code=303)
    return JSONResponse({'detail': 'Sign in or provide an Ayven bearer token.'}, status_code=401)


LOGIN = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ayven · Sign in</title>
<style>body{background:#101521;color:#eef2ff;font:17px system-ui;display:grid;place-items:center;min-height:95vh;margin:0}main{width:min(340px,85vw)}h1{font-size:32px}input,button{box-sizing:border-box;width:100%;padding:14px;border-radius:9px;margin:10px 0;border:1px solid #41506b;font:inherit}button{background:#95b7ff;font-weight:600;cursor:pointer}p{color:#b8c6dc}</style>
<main><h1>Ayven</h1><p>Sign in to your private workforce.</p><form method="post" action="/session"><label for="key">Access key</label><input id="key" name="key" type="password" autocomplete="current-password" required><button>Open Campus</button></form></main></html>'''


def login():
    return HTMLResponse(LOGIN, headers={'Cache-Control': 'no-store'})


async def create_session(request: Request):
    raw = await request.body()
    if len(raw) > 4096:
        return JSONResponse({'detail': 'Invalid access key.'}, status_code=401)
    supplied = parse_qs(raw.decode('utf-8', errors='replace')).get('key', [''])[0]
    if not token() or not hmac.compare_digest(supplied.encode(), token().encode()):
        return JSONResponse({'detail': 'Invalid access key.'}, status_code=401)
    response = RedirectResponse('/', status_code=303)
    response.set_cookie(COOKIE, session_value(int(time.time())), max_age=TTL, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict')
    response.headers['Cache-Control'] = 'no-store'
    return response


def validate_public_configuration():
    if os.environ.get('AYVEN_PUBLIC_MODE') == '1':
        if len(token()) < 32:
            raise RuntimeError('Public deployment requires AYVEN_API_TOKEN of at least 32 characters')
        if os.environ.get('AYVEN_DURABLE') != '1':
            raise RuntimeError('Public deployment requires durable job recovery')
        if os.environ.get('AYVEN_LLM_STUB', '1') == '0' and not os.environ.get('AYVEN_LOCAL_LLM_BASE_URL'):
            raise RuntimeError('Live deployment requires a configured workforce endpoint')
        if not os.environ.get('AYVEN_DB') or os.environ['AYVEN_DB'].startswith('/tmp/'):
            raise RuntimeError('Public deployment requires an explicit persistent AYVEN_DB path')
