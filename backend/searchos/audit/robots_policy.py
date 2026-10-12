"""A read-only view of a robots.txt body for the audit (wildcards, `$`, user-agent groups via protego)."""

from __future__ import annotations

from protego import Protego


class RobotsPolicy:
    def __init__(self, status: str, text: str | None) -> None:
        # missing (4xx) or unreadable: nothing is blocked by robots.txt as far as the audit can tell
        self._parser: Protego | None = Protego.parse(text) if status == "ok" and text is not None else None

    def allows(self, url: str, user_agent: str) -> bool:
        if self._parser is None:
            return True
        return bool(self._parser.can_fetch(url, user_agent))
