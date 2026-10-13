"""Bounded subprocess for testing generated local pages. No model-authored code in the test runner."""
import hashlib
import base64
import json
import os
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'none'; img-src 'self' data:; script-src 'self'; style-src 'self'; form-action 'none'; base-uri 'none'; object-src 'none'; frame-src 'none'")
        super().end_headers()


def chrome_path():
    override = os.environ.get("AYVEN_CHROME_PATH")
    if override:
        return override
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache/ms-playwright")
    for pattern in ("chromium_headless_shell-*/chrome-linux/headless_shell", "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell", "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"):
        for file in cache.glob(pattern):
            return str(file)
    for file in cache.glob("chromium-*/chrome-linux/chrome"):
        return str(file)
    return None


def main(payload):
    from playwright.sync_api import sync_playwright
    checks, screenshots, errors = [], [], []
    files = payload["files"]
    if set(files) != {"index.html", "style.css", "app.js"}:
        raise ValueError("Unexpected file set")
    with tempfile.TemporaryDirectory(prefix="ayven-web-") as folder:
        for name, source in files.items():
            (Path(folder) / name).write_text(source, encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=folder))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=chrome_path())
                for width in (1280, 390):
                    for journey in payload["plan"]["journeys"]:
                        context = browser.new_context(viewport={"width": width, "height": 900}, accept_downloads=False,
                                                      service_workers="block", java_script_enabled=not payload.get("mutant"))
                        context.route("**/*", lambda route: route.continue_() if route.request.url.split("?", 1)[0] in {origin + "/index.html", origin + "/style.css", origin + "/app.js"} else route.abort())
                        page = context.new_page()
                        page.set_default_timeout(2200)
                        page.on("pageerror", lambda error: errors.append(str(error)[:300]))
                        page.on("console", lambda message: errors.append(message.text[:300]) if message.type == "error" else None)
                        result = {"requirement": journey["requirement"], "category": journey["category"], "width": width, "passed": False}
                        try:
                            page.goto(origin + "/index.html", wait_until="networkidle", timeout=5000)
                            steps = journey["steps"]
                            if journey["category"] == "edit":
                                # Establish the existing record through real UI actions,
                                # using the plan's verified creation journey in this
                                # fresh profile. Never inject fixture storage or source.
                                create = next(j for j in payload["plan"]["journeys"] if j["category"] == "create")
                                steps = create["steps"] + steps
                            for step in steps:
                                action = step["action"]
                                target = page.locator(step["selector"]) if action != "reload" else None
                                value = step.get("value", "")
                                if action == "click": target.click()
                                elif action == "fill": target.fill(value)
                                elif action == "select": target.select_option(value)
                                elif action == "check": target.check()
                                elif action == "reload": page.reload(wait_until="networkidle", timeout=5000)
                                elif action == "text":
                                    from playwright.sync_api import expect
                                    expect(target).to_contain_text(value)
                                elif action == "value":
                                    from playwright.sync_api import expect
                                    expect(target).to_have_value(value)
                                elif action == "visible":
                                    from playwright.sync_api import expect
                                    expect(target).to_be_visible()
                                elif action == "invalid":
                                    if target.evaluate("e => e.checkValidity()") is not False:
                                        raise AssertionError("Invalid input was accepted")
                                else: raise ValueError("Unsupported browser action")
                            if page.evaluate("document.documentElement.scrollWidth > innerWidth + 2"):
                                raise AssertionError("Page overflows its responsive viewport")
                            result["passed"] = True
                            if journey is payload["plan"]["journeys"][-1]:
                                png = page.screenshot(full_page=True)
                                screenshots.append({"width": width, "sha256": hashlib.sha256(png).hexdigest(), "bytes": len(png), "png_base64": base64.b64encode(png).decode()})
                        except Exception as exc:
                            result["error"] = f"{type(exc).__name__}: {exc}"[:1000]
                        finally:
                            checks.append(result)
                            context.close()
                    if any(not c["passed"] for c in checks): break
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    passed = bool(checks) and all(c["passed"] for c in checks) and not errors
    return {"passed": passed, "status": "ok" if passed else "failed", "checks": checks,
            "screenshots": screenshots, "errors": errors, "sandbox": "fresh-chromium-profile-network-blocked"}


if __name__ == "__main__":
    try:
        print(json.dumps(main(json.loads(sys.stdin.read()))))
    except Exception as exc:
        print(json.dumps({"passed": False, "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"[-1500:]}))
