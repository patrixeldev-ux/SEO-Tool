"""docs/CHECKS.md is generated from the catalog and must not drift."""

from __future__ import annotations

from pathlib import Path

import pytest

from searchos.audit import gen_docs
from searchos.audit.catalog import load_catalog


def test_checks_doc_is_up_to_date() -> None:
    if not gen_docs.DOCS_PATH.parent.is_dir():
        pytest.skip("docs/ is not part of this checkout")
    assert gen_docs.DOCS_PATH.exists(), "run: python -m searchos.audit.gen_docs"
    assert gen_docs.DOCS_PATH.read_text(encoding="utf-8") == gen_docs.render(), (
        "docs/CHECKS.md is out of date; run: python -m searchos.audit.gen_docs"
    )


def test_every_check_is_in_the_generated_doc_with_its_fix() -> None:
    text = gen_docs.render()
    for info in load_catalog().values():
        assert f"### `{info.id}`" in text
        assert info.how_to_fix in text


def test_check_flag_reports_stale_docs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stale = tmp_path / "CHECKS.md"
    stale.write_text("old")
    monkeypatch.setattr(gen_docs, "DOCS_PATH", stale)
    assert gen_docs.main(["--check"]) == 1
    assert gen_docs.main([]) == 0
    assert gen_docs.main(["--check"]) == 0
