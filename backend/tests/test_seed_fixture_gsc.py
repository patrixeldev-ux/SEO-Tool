"""The fixture-site flavour of the Search Console demo seed lines up with the pages the fixture audit crawls."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

from searchos import dev_seed_gsc
from searchos.devtools.fixture_server import default_fixture_dir


def test_fixture_seed_uses_only_pages_the_fixture_site_serves() -> None:
    rows, totals = dev_seed_gsc.generate(date(2026, 10, 1), fixture=True)
    assert rows and totals
    root = default_fixture_dir()
    pages = {r["page"] for r in rows}
    for page in pages:
        parts = urlsplit(page)
        assert parts.netloc == f"127.0.0.1:{dev_seed_gsc.FIXTURE_PORT}"
        path = parts.path.lstrip("/") or "index.html"
        assert (root / path).is_file(), f"{page} is not a page of the fixture site"


def test_fixture_seed_is_deterministic_and_has_a_decaying_and_a_gapped_page() -> None:
    today = date(2026, 10, 1)
    a, _ = dev_seed_gsc.generate(today, fixture=True)
    b, _ = dev_seed_gsc.generate(today, fixture=True)
    assert a == b
    pages = {r["page"] for r in a}
    assert any(p.endswith("/defects/title-missing.html") for p in pages)
    assert any(p.endswith("/defects/h1-multiple.html") for p in pages)
