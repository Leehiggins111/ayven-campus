"""Preview generated source on an isolated, package-specific loopback origin.

No files, databases, credentials or primary API routes are exposed here.
Stable ports preserve localStorage after the engine restarts. Occupied ports
fail explicitly instead of changing the app's storage origin.
"""
import hashlib
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_servers = {}
_lock = threading.Lock()
CSP = "default-src 'self'; connect-src 'none'; img-src 'self' data:; script-src 'self'; style-src 'self'; form-action 'none'; base-uri 'none'; object-src 'none'; frame-src 'none'; frame-ancestors 'none'"


def preview(package_id: str, files: dict) -> str:
    from .web_artifact import validate_files, hashes
    validate_files(files)
    fingerprint = hashes(files)
    with _lock:
        previous = _servers.get(package_id)
        if previous:
            if previous["hashes"] != fingerprint:
                raise ValueError("The preview source changed; use its new package version")
            return previous["url"]
        # Reserve adjacent ports for the trusted wrapper and untrusted source.
        # Different origins prevent generated code from removing its iframe sandbox.
        port = 22000 + 2 * (int(hashlib.sha256(package_id.encode()).hexdigest()[:8], 16) % 14000)
        token = secrets.token_urlsafe(24)
        sources = {f"/{token}/{name}": body.encode() for name, body in files.items()}
        content_types = {"html": "text/html", "css": "text/css", "js": "text/javascript"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass

            def do_GET(self):
                if self.headers.get("Host") != f"127.0.0.1:{port}":
                    self.send_error(403)
                    return
                path = urlparse(self.path).path
                if path.endswith("/index.html") and self.headers.get("Sec-Fetch-Dest") != "iframe":
                    self.send_error(403, "Open the application through its preview wrapper")
                    return
                body = sources.get(path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_types[path.rsplit(".", 1)[-1]] + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Security-Policy", CSP.replace("frame-ancestors 'none'", f"frame-ancestors http://127.0.0.1:{port + 1}"))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        frame_url = f"http://127.0.0.1:{port}/{token}/index.html"

        class Wrapper(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass

            def do_GET(self):
                if self.headers.get("Host") != f"127.0.0.1:{port + 1}" or urlparse(self.path).path != f"/{token}/index.html":
                    self.send_error(404)
                    return
                body = ("<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
                        "<title>Ayven local application preview</title></head><body>"
                        "<p>Local application preview. Your data stays in this browser on this computer.</p>"
                        f"<iframe title='Generated application' sandbox='allow-scripts allow-same-origin allow-forms' src='{frame_url}' width='100%' height='900'></iframe>"
                        "</body></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Security-Policy", f"default-src 'none'; frame-src http://127.0.0.1:{port}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

        try:
            wrapper = ThreadingHTTPServer(("127.0.0.1", port + 1), Wrapper)
        except OSError:
            server.server_close()
            raise
        threading.Thread(target=server.serve_forever, daemon=True, name=f"ayven-preview-{package_id[:8]}").start()
        threading.Thread(target=wrapper.serve_forever, daemon=True, name=f"ayven-wrapper-{package_id[:8]}").start()
        url = f"http://127.0.0.1:{port + 1}/{token}/index.html"
        _servers[package_id] = {"server": server, "wrapper": wrapper, "url": url, "hashes": fingerprint}
        return url


def close_previews():
    with _lock:
        for entry in _servers.values():
            entry["server"].shutdown()
            entry["server"].server_close()
            entry["wrapper"].shutdown()
            entry["wrapper"].server_close()
        _servers.clear()
