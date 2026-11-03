"""Static server for the local test web apps, bound to 127.0.0.1 on a free port.

    with AppServer() as srv:
        srv.url("form/")                # http://127.0.0.1:PORT/form/
        srv.set_variant("form", "renamed")   # /form/ now serves form/variants/renamed.html

Variants let a test change a page between recording and replay without changing its URL.
/__redirect?to=URL answers 302 to URL (used to test that redirects cannot escape the allowlist).
"""
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/__redirect":
            self.send_response(302)
            self.send_header("Location", parse_qs(parts.query).get("to", ["/"])[0])
            self.end_headers()
            return
        app = parts.path.strip("/").split("/")[0]
        variant = self.server.variants.get(app)
        if variant and parts.path.rstrip("/") in (f"/{app}", f"/{app}/index.html"):
            self.path = f"/{app}/variants/{variant}.html"
        super().do_GET()


class AppServer:
    def __init__(self, root: Path = ROOT):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(root)))
        self.httpd.variants = {}
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def url(self, path: str = "") -> str:
        return f"{self.origin}/{path.lstrip('/')}"

    def set_variant(self, app: str, variant: str | None):
        if variant:
            self.httpd.variants[app] = variant
        else:
            self.httpd.variants.pop(app, None)

    @property
    def allow_entry(self) -> str:
        """The only allowlist entry tests use: this server's exact host:port."""
        return f"127.0.0.1:{self.port}"
