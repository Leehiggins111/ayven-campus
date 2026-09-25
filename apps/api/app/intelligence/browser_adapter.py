"""Read-only Browser Use session. HTTP fetch stays the default elsewhere.

Benchmark use is READ and NAVIGATE only. This adapter never clicks, types,
or submits. file:// URLs are rejected before a browser starts.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
from urllib.parse import urlparse

from .permissions import authorize
from .toolkit import ToolResult, invoke, now

_BLOCKED = ("checkout", "payment", "paypal", "cart", "booking", "purchase", "/buy", "signin", "login")


def available() -> bool:
    return importlib.util.find_spec("browser_use") is not None and bool(chrome_path())


def chrome_path() -> str:
    candidates = [
        os.environ.get("AYVEN_CHROME_PATH", ""),
        "/usr/local/bin/google-chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    cache = os.path.expanduser("~/.cache/ms-playwright")
    if os.path.isdir(cache):
        for root, _dirs, files in os.walk(cache):
            if "chrome" in files and "chromium" in root:
                candidates.append(os.path.join(root, "chrome"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def status() -> dict:
    return {
        "repo": "browser-use/browser-use",
        "licence": "MIT",
        "installed": importlib.util.find_spec("browser_use") is not None,
        "chrome": chrome_path(),
        "active": available(),
        "permissions": "READ/NAVIGATE only",
        "isolation": "headless Chrome, default extensions off, domain allow-list",
    }


def _allowed(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return False
    blob = (parsed.path or "").lower() + " " + (parsed.query or "").lower()
    if any(token in blob for token in _BLOCKED):
        return False
    return True


def open_page(agent_id: str, package_id: str, url: str, approved: bool = False) -> ToolResult:
    def _run() -> ToolResult:
        authorize(agent_id, "browser", approved=approved)
        if not _allowed(url):
            return ToolResult(
                tool="browser",
                status="denied",
                query=url,
                source_url=url,
                error="browser_read_only_http_only",
                timestamp=now(),
                metadata={"adapter": "browser-use", "effect": "none"},
            )
        if not available():
            return ToolResult(
                tool="browser",
                status="unavailable",
                query=url,
                source_url=url,
                error="browser_unavailable",
                timestamp=now(),
                metadata={"adapter": "browser-use", "chrome": chrome_path()},
            )
        try:
            text = asyncio.run(_read(url))
        except Exception as exc:
            return ToolResult(
                tool="browser",
                status="error",
                query=url,
                source_url=url,
                error=f"{type(exc).__name__}: {exc}"[:300],
                timestamp=now(),
                metadata={"adapter": "browser-use"},
            )
        return ToolResult(
            tool="browser",
            status="ok",
            query=url,
            source_url=url,
            extracted_content=(text or "")[:8000],
            timestamp=now(),
            metadata={"adapter": "browser-use", "read_only": True, "evidence_level": "page"},
        )

    try:
        authorize(agent_id, "browser", approved=approved)
    except Exception as exc:
        result = ToolResult(tool="browser", status="denied", query=url, error=str(exc), timestamp=now())
        return invoke(agent_id, "browser", package_id, lambda: result)
    return invoke(agent_id, "browser", package_id, _run)


async def _read(url: str) -> str:
    from browser_use.browser.session import BrowserSession

    host = urlparse(url).hostname or ""
    session = BrowserSession(
        headless=True,
        executable_path=chrome_path(),
        chromium_sandbox=False,
        is_local=True,
        enable_default_extensions=False,
        allowed_domains=[host, "localhost", "127.0.0.1"],
    )
    await session.start()
    try:
        await session.navigate_to(url)
        text = await session.get_state_as_text()
        return text if isinstance(text, str) else str(text)
    finally:
        await session.kill()
