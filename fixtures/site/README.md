# Fixture site

A small fictional coffee-roaster website ("Harbor & Pine") used to test the crawler and the audit.
Every defect page plants exactly one defect (plus the unavoidable knock-on effects, e.g. a thin page is also
indexable); clean pages are the controls.

* `EXPECTED_ISSUES.json` is the exact set of `(check_id, url path)` the audit must report. Site-level issues
  are keyed to `/`. `not_exercised` lists checks this plain-HTTP fixture cannot trigger; they are covered by
  unit tests instead. Entries with `"requires": "browser"` need Chromium.
* `_server.json` holds behaviour that static files cannot express (500s, redirects, `X-Robots-Tag`, a slow
  response, a gzip sitemap). `{{BASE_URL}}` in `.html`, `.xml` and `.txt` files is replaced by the test server.
* Files starting with `_`, `README.md` and `EXPECTED_ISSUES.json` are never served.

If you change a page, update `EXPECTED_ISSUES.json` in the same commit. The golden test
(`backend/tests/test_golden.py`) fails on any difference in either direction.

Serve it by hand: `cd backend && uv run python -m searchos.devtools.fixture_server` (port 8765).
