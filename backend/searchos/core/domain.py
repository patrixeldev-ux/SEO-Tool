"""Domain normalisation for sites: lowercase, strip scheme/path/`www.`, punycode, strict validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import idna

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class InvalidDomainError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedDomain:
    domain: str  # ASCII (punycode), lowercase, no scheme, no "www."
    uses_www: bool  # whether the owner entered the "www." host


def normalize_domain(raw: str) -> NormalizedDomain:
    text = raw.strip()
    if not text:
        raise InvalidDomainError("Domain is empty")
    if any(ch.isspace() for ch in text):
        raise InvalidDomainError("Domain must not contain spaces")

    # urlsplit needs "//" to recognise a bare host; strip any scheme first.
    to_parse = text if "://" in text else f"//{text}"
    try:
        parts = urlsplit(to_parse)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise InvalidDomainError("Not a valid domain") from exc
    if parts.username is not None or parts.password is not None:
        raise InvalidDomainError("Domain must not contain credentials")
    if port is not None:
        raise InvalidDomainError("Ports are not supported; enter the bare domain")
    if not host:
        raise InvalidDomainError("Not a valid domain")

    host = host.strip(".").lower()
    try:
        ascii_host = idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError as exc:
        raise InvalidDomainError("Not a valid internationalised domain name") from exc
    ascii_host = ascii_host.lower()

    uses_www = False
    if ascii_host.startswith("www.") and ascii_host.count(".") >= 2:
        uses_www = True
        ascii_host = ascii_host[4:]

    if len(ascii_host) > 253:
        raise InvalidDomainError("Domain is too long")
    labels = ascii_host.split(".")
    if len(labels) < 2:
        raise InvalidDomainError("Domain must include a top-level domain, e.g. example.com")
    if not all(_LABEL_RE.match(label) for label in labels):
        raise InvalidDomainError("Domain contains invalid characters")
    if labels[-1].isdigit():
        raise InvalidDomainError("IP addresses are not supported; enter a domain name")
    return NormalizedDomain(domain=ascii_host, uses_www=uses_www)
