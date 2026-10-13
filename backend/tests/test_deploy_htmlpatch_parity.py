"""The shared operation cases (edge/test/parity-cases.json) also run against the Python patcher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from searchos.deploy import htmlpatch

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parents[2] / "edge" / "test" / "parity-cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_python_patcher_agrees_with_the_shared_cases(case: dict[str, Any]) -> None:
    out = htmlpatch.apply_ops(case["html"], case["ops"], case["url"])
    for s in case["contains"]:
        assert s in out
    for s in case["absent"]:
        assert s not in out
    # idempotent: a second run adds nothing (our JSON-LD block may move to the end of <head>)
    again = htmlpatch.apply_ops(out, case["ops"], case["url"])
    assert sorted(again.replace("\n", "")) == sorted(out.replace("\n", ""))
