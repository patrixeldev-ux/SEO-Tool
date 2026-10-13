"""A pretend website for development and tests (never used in production).

    python -m searchos.devtools.fake_site --port 8765 [--edge-api http://localhost:8000 --edge-token T --edge-secret S]

Without the `--edge-*` options it serves plain pages. With them it behaves like our Cloudflare Worker: for every
request it fetches the signed rules from the API, checks the signature and the 24-hour limit with the Python twin
of the Worker's check, and applies the operations with `deploy/htmlpatch.py`. Used by the live smoke run
(`python -m searchos.dev_deploy_smoke`) to prove deploy -> verify -> rollback end to end without Cloudflare.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from searchos.deploy import htmlpatch
from searchos.deploy.signing import SIGNATURE_HEADER, normalise_path, verify_rules

PAGES: dict[str, str] = {
    "/": """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Harbor Coffee</title>
<meta name="description" content="Coffee.">
<link rel="canonical" href="/"></head>
<body><h1>Harbor Coffee</h1><p>Welcome to our roastery.</p><a href="/grinders">Grinders</a></body></html>
""",
    "/grinders": """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Grinders</title>
<meta name="description" content="Grinders for sale.">
<link rel="canonical" href="/grinders"></head>
<body><h1>Burr grinders</h1>
<p>We sell burr grinders and fresh beans for home brewing. Read more about espresso machines in our guide.</p>
<img src="/img/grinder.png"></body></html>
""",
}

RulesFetcher = Callable[[], tuple[str, str]]


def http_rules_fetcher(api: str, token: str) -> RulesFetcher:
    def fetch() -> tuple[str, str]:
        with urllib.request.urlopen(f"{api.rstrip('/')}/edge/rules/{token}", timeout=10) as resp:  # noqa: S310
            return resp.read().decode(), resp.headers.get(SIGNATURE_HEADER, "")

    return fetch


def render(
    path: str, pages: dict[str, str], fetch_rules: RulesFetcher | None, secret: str | None
) -> tuple[int, str, str]:
    """(status, body, edge state) for one request, the way the Worker would answer."""
    html = pages.get(normalise_path(path))
    if html is None:
        return 404, "not found", "pass"
    if not fetch_rules or not secret:
        return 200, html, "none"
    try:
        body, sig = fetch_rules()
        rules: dict[str, Any] | None = verify_rules(body, sig, secret)
    except OSError:
        return 200, html, "pass"
    if rules is None:
        return 200, html, "pass"
    if not rules.get("enabled", True):
        return 200, html, "off"
    ops = rules.get("pages", {}).get(normalise_path(path))
    if not ops:
        return 200, html, "ready"
    return 200, htmlpatch.apply_ops(html, ops, f"https://{rules['host']}{normalise_path(path)}"), "on"


def make_server(port: int, edge: tuple[str, str, str] | None = None) -> ThreadingHTTPServer:
    fetch = http_rules_fetcher(edge[0], edge[1]) if edge else None
    secret = edge[2] if edge else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            status, body, state = render(path, PAGES, fetch, secret)
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            if edge:
                self.send_header("x-searchos-edge", state)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--edge-api")
    parser.add_argument("--edge-token")
    parser.add_argument("--edge-secret")
    args = parser.parse_args()
    edge = (args.edge_api, args.edge_token, args.edge_secret) if args.edge_api else None
    server = make_server(args.port, edge)
    print(json.dumps({"serving": f"http://127.0.0.1:{args.port}", "edge": bool(edge)}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
