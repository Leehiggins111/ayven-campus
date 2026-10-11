"""Optional shared-secret gate. Unset key means protection is off and must be reported."""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse

OPEN = {"/health", "/login", "/campus", "/", "/legacy"}


def access_key() -> str:
    return os.environ.get("AYVEN_ACCESS_KEY", "")


def protected() -> bool:
    return bool(access_key())


def _ok(request: Request) -> bool:
    if not protected():
        return True
    path = request.url.path
    if path in OPEN or path.startswith("/r3f"):
        return True
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    cookie = request.cookies.get("ayven_session", "")
    expected = hashlib.sha256(access_key().encode()).hexdigest()
    return _match(supplied, access_key()) or _match(cookie, expected)


def _match(left: str, right: str) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


async def access_middleware(request: Request, call_next):
    if not _ok(request):
        return JSONResponse({"error": "login required"}, status_code=401)
    return await call_next(request)
