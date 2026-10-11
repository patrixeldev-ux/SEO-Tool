"""URL normalisation. Two URLs are the same page for the crawler iff their normalised forms are equal."""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PCT = re.compile(r"%([0-9a-fA-F]{2})")
_SAFE = "/%:@!$&'()*+,;=-._~"


class InvalidUrlError(ValueError):
    pass


def _tidy_percent(match: re.Match[str]) -> str:
    char = chr(int(match.group(1), 16))
    return char if char in _UNRESERVED else "%" + match.group(1).upper()


def _remove_dot_segments(path: str) -> str:
    out: list[str] = []
    segments = path.split("/")
    for i, seg in enumerate(segments):
        if seg == "..":
            if len(out) > 1:
                out.pop()
            if i == len(segments) - 1:
                out.append("")
        elif seg == ".":
            if i == len(segments) - 1:
                out.append("")
        else:
            out.append(seg)
    result = "/".join(out)
    return result if result.startswith("/") else "/" + result


def normalise_url(url: str) -> str:
    """Lower-case scheme and host, drop fragment, userinfo and default port, resolve dot segments,
    normalise percent-encoding. The query string is kept as is (its order can matter to servers)."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise InvalidUrlError(str(exc)) from None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidUrlError(f"unsupported scheme: {scheme!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise InvalidUrlError("missing host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise InvalidUrlError("invalid host name") from None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port is None or port == DEFAULT_PORTS[scheme] else f"{host}:{port}"
    path = _remove_dot_segments(parts.path or "/")
    path = _PCT.sub(_tidy_percent, quote(path, safe=_SAFE))
    query = _PCT.sub(lambda m: "%" + m.group(1).upper(), quote(parts.query, safe=_SAFE + "?"))
    return urlunsplit((scheme, netloc, path, query, ""))


def netloc_of(url: str) -> str:
    """`host[:port]` of a URL in normalised form (used to compare against the allowed hosts)."""
    return urlsplit(normalise_url(url)).netloc


def normalise_netloc(value: str) -> str:
    """Normalise a user-supplied allowed host such as 'Example.com' or '127.0.0.1:8000'."""
    return netloc_of(f"http://{value}/")


def resolve_url(base: str, href: str) -> str | None:
    """Absolute, normalised URL for `href` found on page `base`, or None when it is not an http(s) link."""
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:")):
        return None
    try:
        return normalise_url(urljoin(base, href))
    except (InvalidUrlError, ValueError):
        return None


def path_of(url: str) -> str:
    """Path plus query of a URL, used in reports and in the fixture expectations."""
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "") or "/"
