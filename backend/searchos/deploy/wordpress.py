"""WordPress deployer: talks to the SearchOS Connector plugin (`wp-plugin/searchos-connector`) over its REST API.

Authentication is a WordPress Application Password (Users > Profile). The plugin stores the changes as page
overrides and prints them in the page's HTML, so rolling back is clearing the override: the theme / SEO plugin's
own value returns exactly as it was. The address of the WordPress site must belong to the verified site; the
password only travels over https (plain http is accepted in development).
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

from searchos.deploy.base import ConnectionTest, DeployContext, DeployerError, ItemResult
from searchos.deploy.livefetch import _host_is_public
from searchos.models import Deployment, DeploymentStatus, DeployMethod, Site

TIMEOUT = 25.0
SEO_NOTE = {
    "yoast": "Yoast SEO is active: its title, description and canonical are overridden through its own filters.",
    "rankmath": "Rank Math is active: its title, description and canonical are overridden through its own filters.",
    "none": "No SEO plugin found: the connector prints the tags itself.",
}


def check_site_url(site: Site, site_url: str, *, allow_private: bool) -> str:
    """The normalised WordPress address, or a DeployerError saying what is wrong."""
    parts = urlsplit(site_url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DeployerError("Enter the address of your WordPress site, starting with https://")
    if parts.scheme == "http" and not allow_private:
        raise DeployerError(
            "Use the https:// address of your site: the password must not travel unencrypted."
        )
    host = parts.hostname.lower()
    if host not in (site.domain, f"www.{site.domain}"):
        raise DeployerError(f"The WordPress address must be on {site.domain} (this website), not {host}.")
    if parts.username or parts.password:
        raise DeployerError("Do not put the username or password in the address.")
    path = parts.path.rstrip("/")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{path}"


class WpClient:
    def __init__(self, site_url: str, username: str, password: str, *, allow_private: bool) -> None:
        self.base = site_url.rstrip("/")
        self._auth = (username, password)
        self.allow_private = allow_private

    async def call(
        self, method: str, route: str, *, json: Any = None, params: dict[str, str] | None = None
    ) -> Any:
        host = urlsplit(self.base).hostname or ""
        if not self.allow_private and not await _host_is_public(host):
            raise DeployerError("The WordPress address is private or does not resolve.")
        # Pretty permalinks: /wp-json/...; plain permalinks: /?rest_route=/...
        attempts = [
            (f"{self.base}/wp-json{route}", params),
            (f"{self.base}/", {**(params or {}), "rest_route": route}),
        ]
        last: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, auth=self._auth) as client:
            for url, prm in attempts:
                try:
                    resp = await client.request(method, url, json=json, params=prm)
                except httpx.HTTPError:
                    raise DeployerError("Could not reach your WordPress site.") from None
                last = resp
                if resp.status_code == 404 and _code(resp) == "rest_no_route":
                    continue
                if resp.status_code in (301, 302, 307, 308):
                    raise DeployerError(
                        "Your site redirected the request (check that the address is the final https:// address)."
                    )
                break
        if last is None:  # pragma: no cover - the loop always runs
            raise DeployerError("Could not reach your WordPress site.")
        return self._result(last)

    @staticmethod
    def _result(resp: httpx.Response) -> Any:
        if resp.status_code < 300:
            try:
                return resp.json()
            except ValueError:
                raise DeployerError(
                    "WordPress answered, but not with the SearchOS Connector's reply. Is the plugin active?"
                ) from None
        code = _code(resp)
        if resp.status_code == 401 or code in ("incorrect_password", "invalid_username", "rest_forbidden"):
            if code == "rest_forbidden" and resp.status_code == 403:
                raise DeployerError(
                    "This WordPress user is not allowed to edit pages (needs the Editor role or higher)."
                )
            raise DeployerError(
                "WordPress did not accept the username and application password. Create a new Application "
                "Password in Users > Profile."
            )
        if resp.status_code == 403:
            raise DeployerError(_message(resp) or "This WordPress user may not change that page.")
        if resp.status_code == 404 and code == "rest_no_route":
            raise DeployerError(
                "The SearchOS Connector plugin is not installed or not active on this WordPress site."
            )
        if code.startswith("searchos_"):
            raise DeployerError(_message(resp) or "The SearchOS Connector refused the change.")
        raise DeployerError(f"WordPress answered with an error ({resp.status_code}).")


def _code(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("code", ""))
    except (ValueError, AttributeError):
        return ""


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", ""))[:300]
    except (ValueError, AttributeError):
        return ""


def client_for(ctx: DeployContext) -> WpClient:
    c = ctx.credentials
    try:
        return WpClient(
            c["site_url"],
            c["username"],
            c["application_password"],
            allow_private=ctx.settings.deploy_allow_private_hosts,
        )
    except KeyError:
        raise DeployerError("The WordPress connection is incomplete; connect it again.") from None


FIELD_OF_OP = {"title": "title", "meta_description": "meta_description", "canonical": "canonical"}


def request_body(url: str, ops: list[dict[str, Any]], *, clear: list[str] | None = None) -> dict[str, Any]:
    """Plugin request for a list of ops (later ops of the same field win). `clear` is applied first."""
    body: dict[str, Any] = {
        "url": url,
        "set": {},
        "jsonld": {"put": [], "remove": []},
        "alt": {"put": [], "remove": []},
    }
    if clear:
        body["clear"] = clear
    for op in ops:
        kind = op["op"]
        if kind in FIELD_OF_OP:
            body["set"][FIELD_OF_OP[kind]] = op["value"]
        elif kind == "jsonld":
            body["jsonld"]["put"] = [x for x in body["jsonld"]["put"] if x["id"] != op["id"]] + [
                {"id": op["id"], "value": op["value"], "replace": op.get("replace")}
            ]
        elif kind == "alt":
            body["alt"]["put"] = [x for x in body["alt"]["put"] if x["src"] != op["src"]] + [
                {"src": op["src"], "value": op["value"]}
            ]
    return body


def removal_body(url: str, removed: list[dict[str, Any]], remaining: list[dict[str, Any]]) -> dict[str, Any]:
    """Clear what `removed` ops set, then set again what `remaining` (still live) ops want for the same things."""
    clear = sorted({FIELD_OF_OP[o["op"]] for o in removed if o["op"] in FIELD_OF_OP})
    jsonld_ids = [o["id"] for o in removed if o["op"] == "jsonld"]
    alt_srcs = [o["src"] for o in removed if o["op"] == "alt"]
    body = request_body(
        url,
        [
            o
            for o in remaining
            if FIELD_OF_OP.get(o["op"]) in clear
            or (o["op"] == "jsonld" and o["id"] in jsonld_ids)
            or (o["op"] == "alt" and o["src"] in alt_srcs)
        ],
        clear=clear,
    )
    body["jsonld"]["remove"] = jsonld_ids
    body["alt"]["remove"] = alt_srcs
    return body


class WordPressDeployer:
    method = DeployMethod.wordpress
    unsupported_ops = {
        "link": "The WordPress connector cannot insert internal links yet. Use the developer hand-off for this one."
    }

    async def apply(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        wp = client_for(ctx)
        by_url: dict[str, list[Deployment]] = {}
        for d in items:
            by_url.setdefault(d.url, []).append(d)
        out: dict[uuid.UUID, ItemResult] = {}
        for url, group in by_url.items():
            ops = [op for d in group for op in d.payload.get("ops", [])]
            try:
                answer = await wp.call("PUT", "/searchos/v1/overrides", json=request_body(url, ops))
            except DeployerError as exc:
                for d in group:
                    out[d.id] = ItemResult(False, DeploymentStatus.failed, str(exc))
                continue
            post_id = (answer.get("target") or {}).get("post_id")
            for d in group:
                out[d.id] = ItemResult(
                    True,
                    DeploymentStatus.applied,
                    meta={"wp_post_id": post_id, "wp_before": answer.get("before")},
                )
        return out

    async def rollback(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        wp = client_for(ctx)
        by_url: dict[str, list[Deployment]] = {}
        for d in items:
            by_url.setdefault(d.url, []).append(d)
        out: dict[uuid.UUID, ItemResult] = {}
        for url, group in by_url.items():
            removed = [op for d in group for op in d.payload.get("ops", [])]
            remaining = [op for d in ctx.live.get(url, []) for op in d.payload.get("ops", [])]
            try:
                await wp.call("PUT", "/searchos/v1/overrides", json=removal_body(url, removed, remaining))
            except DeployerError as exc:
                for d in group:
                    out[d.id] = ItemResult(False, DeploymentStatus.failed, str(exc))
                continue
            for d in group:
                out[d.id] = ItemResult(True, DeploymentStatus.reverted)
        return out

    async def test_connection(self, ctx: DeployContext) -> ConnectionTest:
        try:
            status = await client_for(ctx).call("GET", "/searchos/v1/status")
        except DeployerError as exc:
            return ConnectionTest(False, str(exc))
        if not isinstance(status, dict) or "plugin_version" not in status:
            return ConnectionTest(
                False, "WordPress answered, but not like the SearchOS Connector. Is it active?"
            )
        seo = str(status.get("seo_plugin", "none"))
        return ConnectionTest(
            True,
            f"Connected to WordPress {status.get('wp_version', '')} as {status.get('user', '')}. "
            + SEO_NOTE.get(seo, SEO_NOTE["none"]),
            {
                "wp_version": str(status.get("wp_version", "")),
                "plugin_version": str(status.get("plugin_version", "")),
                "user": str(status.get("user", "")),
            },
        )
