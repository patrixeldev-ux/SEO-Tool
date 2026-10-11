"""robots.txt handling on top of `protego` (wildcards, `$`, longest-match Allow/Disallow, UA groups).

Failure semantics follow Google's documentation:
* 2xx          -> parse and obey the rules
* 4xx (not 429) -> no robots.txt: everything is allowed ("missing")
* 5xx / 429 / network error -> treat as "disallow everything" for this crawl ("error")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from protego import Protego

from searchos.crawl import ROBOTS_PRODUCT

MAX_ROBOTS_BYTES = 512 * 1024
RobotsStatus = Literal["ok", "missing", "error"]


@dataclass(slots=True)
class RobotsRules:
    status: RobotsStatus
    text: str | None = None
    http_status: int | None = None
    _parser: Protego | None = None

    @classmethod
    def from_text(cls, text: str, http_status: int | None = 200) -> RobotsRules:
        return cls(status="ok", text=text, http_status=http_status, _parser=Protego.parse(text))

    @classmethod
    def missing(cls, http_status: int | None = 404) -> RobotsRules:
        return cls(status="missing", http_status=http_status)

    @classmethod
    def error(cls, http_status: int | None = None) -> RobotsRules:
        return cls(status="error", http_status=http_status)

    def can_fetch(self, url: str, user_agent: str = ROBOTS_PRODUCT) -> bool:
        if self.status == "missing":
            return True
        if self.status == "error" or self._parser is None:
            return False
        return bool(self._parser.can_fetch(url, user_agent))

    @property
    def sitemaps(self) -> list[str]:
        if self._parser is None:
            return []
        return [str(s) for s in self._parser.sitemaps]

    def crawl_delay(self, user_agent: str = ROBOTS_PRODUCT) -> float | None:
        if self._parser is None:
            return None
        delay = self._parser.crawl_delay(user_agent)
        return float(delay) if delay is not None else None


def rules_from_response(status: int | None, body: bytes | None) -> RobotsRules:
    """Map a robots.txt HTTP response onto rules (see module docstring)."""
    if status is None:
        return RobotsRules.error()
    if 200 <= status < 300:
        text = (body or b"")[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace")
        return RobotsRules.from_text(text, status)
    if 400 <= status < 500 and status != 429:
        return RobotsRules.missing(status)
    return RobotsRules.error(status)
