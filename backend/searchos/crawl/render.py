"""Rendering pages in a real browser, only for the "is the content there without JavaScript?" check.

AI crawlers such as GPTBot, ClaudeBot and PerplexityBot do not run JavaScript. We compare the text of the raw
HTML with the text of the page after a browser has run its scripts. The browser is optional: when Chromium is
not installed the check is skipped and the audit says so.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol
from urllib.parse import urlsplit

from searchos.crawl import USER_AGENT
from searchos.crawl.urls import InvalidUrlError, normalise_url

log = logging.getLogger(__name__)

MAX_RENDERED_PAGES = 50
RENDER_TIMEOUT_MS = 20_000


class Renderer(Protocol):
    async def start(self) -> bool:
        """Launch the browser. False when it cannot run (not installed, no permission, ...)."""
        ...

    async def render(self, url: str) -> str | None:
        """The page's HTML after scripts ran, or None if it could not be rendered."""
        ...

    async def aclose(self) -> None: ...


def sample_pages(candidates: list[tuple[str, int | None]], limit: int = MAX_RENDERED_PAGES) -> list[str]:
    """Up to `limit` URLs, spread evenly over the site (shallow pages first), the same every time."""
    ordered = sorted(candidates, key=lambda c: (c[1] if c[1] is not None else 99, c[0]))
    urls = [url for url, _ in ordered]
    if len(urls) <= limit:
        return urls
    return [urls[int(i * len(urls) / limit)] for i in range(limit)]


class PlaywrightRenderer:
    """Chromium via Playwright. Requests to hosts other than the audited ones are blocked."""

    def __init__(
        self,
        allowed_hosts: frozenset[str],
        *,
        timeout_ms: int = RENDER_TIMEOUT_MS,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._allowed = allowed_hosts
        self._timeout_ms = timeout_ms
        self._user_agent = user_agent
        self._playwright: Any = None
        self._browser: Any = None
        self.error: str | None = None

    async def start(self) -> bool:
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            # Inside Docker we run as root, where Chromium refuses to start with its sandbox on.
            root = hasattr(os, "geteuid") and os.geteuid() == 0
            args = ["--no-sandbox", "--disable-dev-shm-usage"] if root else ["--disable-dev-shm-usage"]
            self._browser = await self._playwright.chromium.launch(headless=True, args=args)
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"[:200]
            log.warning("browser unavailable: %s", self.error)
            await self.aclose()
            return False

    def _host_allowed(self, url: str) -> bool:
        if url.startswith(("data:", "blob:", "about:")):
            return True
        try:
            return urlsplit(normalise_url(url)).netloc in self._allowed
        except InvalidUrlError:
            return False

    async def render(self, url: str) -> str | None:
        if self._browser is None:
            return None
        context = await self._browser.new_context(user_agent=self._user_agent, java_script_enabled=True)
        try:

            async def guard(route: Any, request: Any) -> None:
                if self._host_allowed(request.url):
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", guard)
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=self._timeout_ms)
            return str(await page.content())
        except Exception as exc:
            log.info("could not render %s: %s", url, type(exc).__name__)
            return None
        finally:
            await context.close()

    async def aclose(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        try:
            if browser is not None:
                await browser.close()
            if playwright is not None:
                await playwright.stop()
        except Exception:
            log.debug("error while closing the browser", exc_info=True)
