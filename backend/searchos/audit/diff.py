"""Compare two audits: which issues are new, which were fixed and which persist."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

# (check_id, page URL) — the URL is "" for site-level issues.
IssueKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class IssueDiff:
    new: list[IssueKey] = field(default_factory=list)
    fixed: list[IssueKey] = field(default_factory=list)
    persisting: list[IssueKey] = field(default_factory=list)
    not_rechecked: list[IssueKey] = field(
        default_factory=list
    )  # old issue on a page this crawl did not reach


def diff_issues(
    previous: Collection[IssueKey], current: Collection[IssueKey], crawled_now: Collection[str]
) -> IssueDiff:
    """`crawled_now` = URLs the current crawl fetched. A previous issue on a URL that was not crawled this
    time is neither fixed nor persisting (we simply do not know), so it is reported as not re-checked."""
    prev, cur, crawled = set(previous), set(current), set(crawled_now)
    fixed: list[IssueKey] = []
    unknown: list[IssueKey] = []
    for key in sorted(prev - cur):
        (fixed if key[1] == "" or key[1] in crawled else unknown).append(key)
    return IssueDiff(
        new=sorted(cur - prev),
        fixed=fixed,
        persisting=sorted(prev & cur),
        not_rechecked=unknown,
    )
