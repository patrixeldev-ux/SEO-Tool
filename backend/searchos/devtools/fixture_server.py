"""A tiny HTTP server for `fixtures/site/`.

Static files are served as they are, except that `{{BASE_URL}}` is replaced (in .html/.xml/.txt) by the
server's real base URL, so pages can contain absolute URLs without knowing the port.

Behaviour that static files cannot express lives in `fixtures/site/_server.json`, a map of URL path to rule:

    status       HTTP status to answer with (default 200, or 301 when `redirect_to` is set)
    redirect_to  path (or absolute URL) for the Location header
    headers      extra response headers, e.g. {"X-Robots-Tag": "noindex"}
    delay_ms     sleep this long before answering (slow TTFB)
    gzip_of      serve the gzip of another static file (path relative to the fixture root)

Files whose name starts with "_" and EXPECTED_ISSUES.json are never served.
"""

from __future__ import annotations

import gzip
import json
import mimetypes
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

TEMPLATED_SUFFIXES = {".html", ".xml", ".txt"}
HIDDEN_NAMES = {"EXPECTED_ISSUES.json", "README.md"}

_EXTRA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".gz": "application/gzip",
}


def default_fixture_dir() -> Path:
    env = os.environ.get("FIXTURE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "fixtures" / "site"


@dataclass(slots=True)
class RequestRecord:
    method: str
    path: str  # path + query as received
    user_agent: str | None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FixtureServer:
    def __init__(
        self,
        root: Path | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        rules: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.root = (root or default_fixture_dir()).resolve()
        self.requests: list[RequestRecord] = []
        self._lock = threading.Lock()
        rules_file = self.root / "_server.json"
        self.rules: dict[str, dict[str, Any]] = (
            json.loads(rules_file.read_text(encoding="utf-8")) if rules_file.exists() else {}
        )
        if rules:
            self.rules.update(rules)
        self._host = host
        self._httpd = ThreadingHTTPServer((host, port), self._make_handler())
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------------------------------
    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self.port}"

    @property
    def netloc(self) -> str:
        return f"{self._host}:{self.port}"

    def start(self) -> FixtureServer:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="fixture-server")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> FixtureServer:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # ---- request log -----------------------------------------------------------------------------
    def requested_paths(self) -> list[str]:
        with self._lock:
            return [r.path for r in self.requests]

    def _record(self, record: RequestRecord) -> None:
        with self._lock:
            self.requests.append(record)

    # ---- file resolution -------------------------------------------------------------------------
    def _static_file(self, url_path: str) -> Path | None:
        rel = url_path.lstrip("/")
        candidate = self.root / rel
        if url_path.endswith("/") or rel == "":
            candidate = candidate / "index.html"
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (ValueError, OSError):
            return None
        if not resolved.is_file():
            return None
        if resolved.name.startswith("_") or resolved.name in HIDDEN_NAMES:
            return None
        return resolved

    def _read(self, path: Path) -> tuple[bytes, str]:
        content_type = _EXTRA_TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "text/plain"
        data = path.read_bytes()
        if path.suffix in TEMPLATED_SUFFIXES:
            data = data.replace(b"{{BASE_URL}}", self.base_url.encode())
        return data, content_type

    def _absolute(self, target: str) -> str:
        return target if "://" in target else f"{self.base_url}{target}"

    # ---- handler ---------------------------------------------------------------------------------
    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _respond(self, head_only: bool) -> None:
                server._record(RequestRecord(self.command, self.path, self.headers.get("User-Agent")))
                url_path = urlsplit(self.path).path or "/"
                rule = server.rules.get(url_path, {})
                if rule.get("delay_ms"):
                    time.sleep(rule["delay_ms"] / 1000)

                status = int(rule.get("status", 301 if "redirect_to" in rule else 200))
                headers: dict[str, str] = dict(rule.get("headers", {}))
                body = b""
                content_type = "text/plain; charset=utf-8"

                if "redirect_to" in rule:
                    headers["Location"] = server._absolute(rule["redirect_to"])
                    body = b"Redirecting"
                elif "gzip_of" in rule:
                    source = server._static_file("/" + rule["gzip_of"])
                    if source is None:
                        status, body = 404, b"Not found"
                    else:
                        raw, _ = server._read(source)
                        body = gzip.compress(raw)
                        content_type = "application/gzip"
                else:
                    static = server._static_file(url_path)
                    if static is not None:
                        body, content_type = server._read(static)
                    elif "status" not in rule:
                        status, body = 404, b"Not found"
                    else:
                        body = b"Error"

                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

            def do_GET(self) -> None:
                self._respond(head_only=False)

            def do_HEAD(self) -> None:
                self._respond(head_only=True)

        return Handler


def serve_forever(port: int = 8765) -> None:  # pragma: no cover - manual use
    with FixtureServer(port=port) as srv:
        print(f"Fixture site on {srv.base_url} (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":  # pragma: no cover
    serve_forever()
