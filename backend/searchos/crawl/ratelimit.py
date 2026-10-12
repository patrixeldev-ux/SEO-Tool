"""Per-host politeness: at most `rate` requests per second to any single host."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class HostRateLimiter:
    def __init__(
        self,
        rate_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleep = sleep
        self._next_slot: dict[str, float] = {}
        self._min_interval: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def set_min_interval(self, host: str, seconds: float) -> None:
        """Honour a robots.txt Crawl-delay: never faster than this for `host` (it only ever slows us down)."""
        self._min_interval[host] = max(0.0, seconds)

    def interval_for(self, host: str) -> float:
        return max(self._interval, self._min_interval.get(host, 0.0))

    async def wait(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:  # FIFO: concurrent callers for one host take turns
            now = self._clock()
            delay = self._next_slot.get(host, 0.0) - now
            if delay > 0:
                await self._sleep(delay)
                now = self._clock()
            self._next_slot[host] = now + self.interval_for(host)
