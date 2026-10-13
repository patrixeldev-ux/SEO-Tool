"""Signed rule sets for the edge Worker.

The Worker asks `GET /edge/rules/{site_token}` and gets a JSON body plus a header
`X-SearchOS-Signature: <hex HMAC-SHA256(site secret, body bytes)>`. The body says when it was issued, so a
Worker that cannot reach us any more still stops applying rules after 24 hours. `edge/src/rules.ts` implements
the same functions; `edge/test/vectors.json` holds test vectors that both sides check, so the Python and
TypeScript versions cannot drift apart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit

MAX_AGE_SECONDS = 24 * 3600
CLOCK_SKEW_SECONDS = 300
SIGNATURE_HEADER = "X-SearchOS-Signature"


def new_site_token() -> str:
    return secrets.token_urlsafe(24)


def new_signing_secret() -> str:
    return secrets.token_hex(32)


def normalise_path(url_or_path: str) -> str:
    """The path of a page as both sides key it: percent-encoding normalised per segment, query and fragment
    dropped, no trailing slash except for the home page. Case is kept (paths are case-sensitive)."""
    path = urlsplit(url_or_path.strip()).path or "/"
    segments = [quote(unquote(seg), safe="-_.~") for seg in path.split("/")]
    path = "/".join(segments)
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path


def sign_body(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def build_rules(
    *,
    host: str,
    version: int,
    issued_at: int,
    enabled: bool,
    pages: dict[str, list[dict[str, Any]]],
) -> str:
    """The exact body that is signed and sent. `pages` is keyed by `normalise_path`."""
    if not enabled:
        pages = {}
    return json.dumps(
        {"v": version, "issued_at": issued_at, "enabled": enabled, "host": host, "pages": pages},
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    )


def verify_rules(body: str, signature: str, secret: str, *, now: int | None = None) -> dict[str, Any] | None:
    """Python twin of the Worker's check (tests and the dev edge simulator): None when unsigned, wrongly signed,
    malformed or older than 24 hours."""
    if not hmac.compare_digest(sign_body(body, secret), signature or ""):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("issued_at"), int):
        return None
    t = int(time.time()) if now is None else now
    if t - data["issued_at"] > MAX_AGE_SECONDS or data["issued_at"] - t > CLOCK_SKEW_SECONDS:
        return None
    return data
