"""Proof that the person who added a site controls it. Nothing is deployed to a site that has not passed this.

Two methods, either is enough:
* DNS: a TXT record on the apex domain with the value `searchos-verify=<token>`;
* file: `https://<domain>/searchos-verify-<token>.txt` whose body is the token.

The token is random per site. The proof is re-checked before a deploy when the last check is older than 7 days.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import dns.asyncresolver
import dns.exception
import dns.resolver

from searchos.deploy.livefetch import PageFetcher, default_fetcher
from searchos.models import Site

TXT_PREFIX = "searchos-verify="
RECHECK_AFTER = timedelta(days=7)
Method = Literal["dns", "file"]


class NotVerifiedError(Exception):
    """The site's ownership is not (or no longer) proven. `str(error)` is a sentence for the person."""


class TxtResolver(Protocol):
    async def txt(self, name: str) -> list[str]: ...


class DnsPythonResolver:
    async def txt(self, name: str) -> list[str]:
        resolver = dns.asyncresolver.Resolver()
        resolver.lifetime = 8.0
        try:
            answer = await resolver.resolve(name, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return []
        except dns.exception.DNSException:
            return []
        out: list[str] = []
        for rdata in answer:
            out.append(b"".join(rdata.strings).decode("utf-8", errors="replace"))
        return out


@dataclass(slots=True)
class Checkers:
    resolver: TxtResolver
    fetcher: PageFetcher


def default_checkers() -> Checkers:
    return Checkers(DnsPythonResolver(), default_fetcher())


@dataclass(frozen=True, slots=True)
class OwnershipResult:
    ok: bool
    method: Method | None
    error: str | None = None


def new_token() -> str:
    return secrets.token_urlsafe(24)


def dns_value(token: str) -> str:
    return f"{TXT_PREFIX}{token}"


def file_path(token: str) -> str:
    return f"/searchos-verify-{token}.txt"


def file_urls(domain: str, uses_www: bool, token: str) -> list[str]:
    hosts = [f"www.{domain}", domain] if uses_www else [domain, f"www.{domain}"]
    return [f"https://{h}{file_path(token)}" for h in hosts]


async def check_dns(domain: str, token: str, resolver: TxtResolver) -> bool:
    want = dns_value(token)
    return any(record.strip().strip('"') == want for record in await resolver.txt(domain))


async def check_file(domain: str, uses_www: bool, token: str, fetcher: PageFetcher) -> bool:
    for url in file_urls(domain, uses_www, token):
        resp = await fetcher.get(url, no_cache=True)
        if resp.status == 200 and resp.text.strip() == token:
            return True
    return False


async def check_ownership(
    domain: str,
    uses_www: bool,
    token: str,
    checkers: Checkers,
    *,
    methods: tuple[Method, ...] = ("dns", "file"),
) -> OwnershipResult:
    for method in methods:
        ok = (
            await check_dns(domain, token, checkers.resolver)
            if method == "dns"
            else await check_file(domain, uses_www, token, checkers.fetcher)
        )
        if ok:
            return OwnershipResult(True, method)
    return OwnershipResult(
        False,
        None,
        "Neither proof was found. Add the DNS record or upload the file shown on this page, then check again "
        "(DNS changes can take a few minutes to appear).",
    )


def ensure_token(site: Site) -> str:
    if not site.verify_token:
        site.verify_token = new_token()
    return site.verify_token


def is_verified(site: Site) -> bool:
    return site.verified_at is not None


def is_fresh(site: Site, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return (
        site.verified_at is not None
        and site.verify_checked_at is not None
        and now - site.verify_checked_at <= RECHECK_AFTER
    )


async def run_check(
    site: Site,
    checkers: Checkers,
    *,
    methods: tuple[Method, ...] = ("dns", "file"),
    now: datetime | None = None,
) -> OwnershipResult:
    """Check now and record the outcome on the site row (the caller commits)."""
    now = now or datetime.now(UTC)
    token = ensure_token(site)
    result = await check_ownership(site.domain, site.uses_www, token, checkers, methods=methods)
    site.verify_checked_at = now
    if result.ok:
        if site.verified_at is None:
            site.verified_at = now
        site.verify_method = result.method
        site.verify_error = None
    else:
        site.verified_at = None
        site.verify_error = result.error
    return result


async def require_verified(
    site: Site, checkers: Checkers | None = None, *, now: datetime | None = None
) -> None:
    """The gate in front of every deploy: unverified -> NotVerifiedError; a proof older than 7 days is re-checked."""
    now = now or datetime.now(UTC)
    if site.verified_at is None:
        raise NotVerifiedError(
            "This site's ownership has not been verified yet. Open Settings > Deploy and add the DNS record "
            "or the verification file."
        )
    if is_fresh(site, now):
        return
    preferred: tuple[Method, ...] = ("file", "dns") if site.verify_method == "file" else ("dns", "file")
    result = await run_check(site, checkers or default_checkers(), methods=preferred, now=now)
    if not result.ok:
        raise NotVerifiedError(
            "The ownership proof for this site could not be found any more (it is re-checked every 7 days). "
            "Restore the DNS record or file in Settings > Deploy."
        )
