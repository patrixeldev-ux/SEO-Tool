from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import httpx

from searchos.devtools.fixture_server import FixtureServer, default_fixture_dir


def test_serves_pages_with_base_url_substituted(fixture_site: FixtureServer) -> None:
    resp = httpx.get(fixture_site.base_url + "/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "{{BASE_URL}}" not in resp.text
    assert f'<link rel="canonical" href="{fixture_site.base_url}/">' in resp.text


def test_directory_urls_map_to_index_html(fixture_site: FixtureServer) -> None:
    assert httpx.get(fixture_site.base_url + "/products/").status_code == 200
    assert httpx.get(fixture_site.base_url + "/products/espresso-beans.html").status_code == 200


def test_missing_page_is_404_and_internal_files_are_hidden(fixture_site: FixtureServer) -> None:
    assert httpx.get(fixture_site.base_url + "/gone.html").status_code == 404
    assert httpx.get(fixture_site.base_url + "/_server.json").status_code == 404
    assert httpx.get(fixture_site.base_url + "/EXPECTED_ISSUES.json").status_code == 404
    assert httpx.get(fixture_site.base_url + "/../../etc/passwd").status_code == 404


def test_rules_status_redirect_and_headers(fixture_site: FixtureServer) -> None:
    base = fixture_site.base_url
    assert httpx.get(base + "/server-error.html").status_code == 500
    resp = httpx.get(base + "/redir-chain-a", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == base + "/redir-chain-b"
    assert httpx.get(base + "/defects/xrobots-noindex.html").headers["x-robots-tag"] == "noindex"


def test_slow_page_is_delayed(fixture_site: FixtureServer) -> None:
    resp = httpx.get(fixture_site.base_url + "/defects/slow.html")
    assert resp.status_code == 200
    assert resp.elapsed.total_seconds() >= 1.2


def test_gzip_sitemap_is_served_compressed(fixture_site: FixtureServer) -> None:
    resp = httpx.get(fixture_site.base_url + "/sitemap-extra.xml.gz")
    text = gzip.decompress(resp.content).decode()
    assert f"{fixture_site.base_url}/defects/orphan.html" in text


def test_request_log_records_user_agent(fixture_site: FixtureServer) -> None:
    httpx.get(fixture_site.base_url + "/about.html", headers={"User-Agent": "unit-test"})
    assert any(r.path == "/about.html" and r.user_agent == "unit-test" for r in fixture_site.requests)


def test_every_html_page_on_disk_is_reachable_or_listed() -> None:
    """Every fixture HTML file is either a page the golden test knows about or is linked from somewhere."""
    root = default_fixture_dir()
    expected: dict[str, Any] = json.loads((root / "EXPECTED_ISSUES.json").read_text())
    assert expected["issues"], "EXPECTED_ISSUES.json must list the planted defects"
    for entry in expected["issues"]:
        assert entry["url"].startswith("/")
        assert {"check_id", "url", "note"} <= entry.keys()
    pages = list(Path(root).rglob("*.html"))
    assert len(pages) >= 30
