"""Check registry: every audit check registers itself under its id with a decorator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from searchos.audit.model import AuditInput, PageData


@dataclass(frozen=True, slots=True)
class Failure:
    """One failed unit. `page_id` is None for site-level findings."""

    page_id: str | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    applicable: int  # how many units the check was evaluated on (pages, or 1 for a site-level check)
    failures: list[Failure]


CheckFn = Callable[[AuditInput], CheckOutcome]
PagePredicate = Callable[[PageData, AuditInput], bool]
PageTest = Callable[[PageData, AuditInput], dict[str, Any] | None]
SiteTest = Callable[[AuditInput], dict[str, Any] | None]
SiteApplies = Callable[[AuditInput], bool]


@dataclass(frozen=True, slots=True)
class RegisteredCheck:
    check_id: str
    applies_to: str
    run: CheckFn


_REGISTRY: dict[str, RegisteredCheck] = {}


def _add(check_id: str, applies_to: str, run: CheckFn) -> None:
    if check_id in _REGISTRY:
        raise ValueError(f"check {check_id} registered twice")
    _REGISTRY[check_id] = RegisteredCheck(check_id, applies_to, run)


def _fetched(p: PageData, _a: AuditInput) -> bool:
    return p.fetch_error != "blocked_by_robots" and (p.status_code is not None or p.fetch_error is not None)


def _internal_canonical(p: PageData, a: AuditInput) -> bool:
    return p.is_indexable and bool(p.canonical) and a.is_internal(p.canonical or "")


def _psi_value(p: PageData, name: str) -> object:
    psi = p.extra.get("psi")
    return psi.get(name) if isinstance(psi, dict) else None


# Page populations a check can apply to. The names are the `applies_to` values in catalog.yaml.
PAGE_POPULATIONS: dict[str, PagePredicate] = {
    "fetched_urls": _fetched,
    "responding_urls": lambda p, a: p.status_code is not None,
    "html_pages": lambda p, a: p.is_success_html,
    "indexable_pages": lambda p, a: p.is_indexable,
    "https_pages": lambda p, a: p.is_success_html and p.normalised_url.startswith("https://"),
    "internal_canonical_pages": _internal_canonical,
    "hreflang_pages": lambda p, a: p.is_indexable and bool(p.hreflang),
    "sitemap_urls": lambda p, a: p.in_sitemap,
    "sitemap_pages": lambda p, a: p.in_sitemap and p.is_success_html,
    "article_pages": lambda p, a: p.is_indexable and bool(p.extra.get("is_article")),
    "question_pages": lambda p, a: p.is_indexable and bool(p.extra.get("question_headings")),
    "rendered_pages": lambda p, a: p.is_indexable and p.extra.get("rendered_word_count") is not None,
    # Core Web Vitals come from PageSpeed Insights results attached to the crawl; no data = not applicable.
    "psi_lcp_pages": lambda p, a: p.is_indexable and _psi_value(p, "lcp_ms") is not None,
    "psi_inp_pages": lambda p, a: p.is_indexable and _psi_value(p, "inp_ms") is not None,
    "psi_cls_pages": lambda p, a: p.is_indexable and _psi_value(p, "cls") is not None,
}
SITE_POPULATIONS = frozenset({"site", "homepage"})


def page_check(check_id: str, applies_to: str) -> Callable[[PageTest], PageTest]:
    """Register a per-page check. The function returns None when the page passes, or a dict of details."""
    if applies_to not in PAGE_POPULATIONS:
        raise ValueError(f"unknown page population {applies_to!r}")
    population = PAGE_POPULATIONS[applies_to]

    def decorator(test: PageTest) -> PageTest:
        def run(audit: AuditInput) -> CheckOutcome:
            applicable = [p for p in audit.pages if population(p, audit)]
            failures = []
            for page in applicable:
                details = test(page, audit)
                if details is not None:
                    failures.append(Failure(page.id, details))
            return CheckOutcome(len(applicable), failures)

        _add(check_id, applies_to, run)
        return test

    return decorator


def group_check(
    check_id: str, applies_to: str
) -> Callable[
    [Callable[[list[PageData], AuditInput], list[Failure]]],
    Callable[[list[PageData], AuditInput], list[Failure]],
]:
    """Register a check that needs to compare pages with each other (duplicates). The function receives the
    applicable pages and returns the failures."""
    population = PAGE_POPULATIONS[applies_to]

    def decorator(
        test: Callable[[list[PageData], AuditInput], list[Failure]],
    ) -> Callable[[list[PageData], AuditInput], list[Failure]]:
        def run(audit: AuditInput) -> CheckOutcome:
            applicable = [p for p in audit.pages if population(p, audit)]
            return CheckOutcome(len(applicable), test(applicable, audit))

        _add(check_id, applies_to, run)
        return test

    return decorator


def site_check(
    check_id: str, applies_to: str = "site", applicable: SiteApplies | None = None
) -> Callable[[SiteTest], SiteTest]:
    """Register a site-level check (one unit). `applicable` can switch it off when it cannot be evaluated."""
    if applies_to not in SITE_POPULATIONS:
        raise ValueError(f"unknown site population {applies_to!r}")

    def decorator(test: SiteTest) -> SiteTest:
        def run(audit: AuditInput) -> CheckOutcome:
            if applicable is not None and not applicable(audit):
                return CheckOutcome(0, [])
            details = test(audit)
            if details is None:
                return CheckOutcome(1, [])
            homepage = audit.homepage if applies_to == "homepage" else None
            return CheckOutcome(1, [Failure(homepage.id if homepage else None, details)])

        _add(check_id, applies_to, run)
        return test

    return decorator


def registered_checks() -> dict[str, RegisteredCheck]:
    # Importing the check modules registers them.
    from searchos.audit import checks_page, checks_site  # noqa: F401

    return dict(_REGISTRY)
