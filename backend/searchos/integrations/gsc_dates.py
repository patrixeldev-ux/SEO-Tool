"""Which days of Search Console data to fetch. Pure date arithmetic.

Google's search analytics are final about two days after the day ends, and only the last 16 months are kept.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

RETENTION_MONTHS = 16
FINALIZATION_LAG_DAYS = 2  # the newest day we ask for is today - 2
REFETCH_DAYS = 5  # each incremental sync re-fetches this many of the newest days (they finalise late)
# An empty answer for a day newer than this is "not published yet", not "zero traffic".
UNPUBLISHED_WINDOW_DAYS = 4


def subtract_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def latest_day(today: date) -> date:
    return today - timedelta(days=FINALIZATION_LAG_DAYS)


def earliest_day(today: date) -> date:
    return subtract_months(today, RETENTION_MONTHS)


def is_unpublished_window(day: date, today: date) -> bool:
    return day > today - timedelta(days=UNPUBLISHED_WINDOW_DAYS)


def days_between(start: date, end: date) -> list[date]:
    """Inclusive, oldest first."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)] if end >= start else []


def days_to_fetch(
    today: date, existing: set[date], *, mode: str, start: date | None = None, end: date | None = None
) -> list[date]:
    """Days a sync must fetch, newest first (so recent data shows up first while a backfill runs).

    - backfill: every day in [start or 16 months ago, end or latest] without a stored total (resumable).
    - incremental: those missing days, plus the newest `REFETCH_DAYS` days again.
    """
    last = min(end or latest_day(today), latest_day(today))
    first = max(start or earliest_day(today), earliest_day(today))
    wanted = set(days_between(first, last))
    todo = {d for d in wanted if d not in existing}
    if mode == "incremental":
        todo |= set(days_between(max(first, last - timedelta(days=REFETCH_DAYS - 1)), last))
    return sorted(todo, reverse=True)
