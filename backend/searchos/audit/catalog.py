"""Loads `catalog.yaml`: the description, severity and category of every check."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

import yaml

from searchos.audit.model import CATEGORIES, SEVERITIES, Category, Severity

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")

# Weight of one failing check by severity (the scoring formula's wᵢ).
SEVERITY_WEIGHTS: dict[Severity, int] = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}


@dataclass(frozen=True, slots=True)
class CheckInfo:
    id: str
    category: Category
    severity: Severity
    title: str
    why_it_matters: str
    how_to_fix: str
    applies_to: str

    @property
    def weight(self) -> int:
        return SEVERITY_WEIGHTS[self.severity]


class CatalogError(ValueError):
    pass


def parse_catalog(text: str) -> dict[str, CheckInfo]:
    raw = yaml.safe_load(text)
    if not isinstance(raw, list):
        raise CatalogError("catalog.yaml must be a list of checks")
    catalog: dict[str, CheckInfo] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise CatalogError(f"catalog entry is not a mapping: {entry!r}")
        missing = {
            "id",
            "category",
            "severity",
            "title",
            "why_it_matters",
            "how_to_fix",
            "applies_to",
        } - entry.keys()
        if missing:
            raise CatalogError(f"{entry.get('id', '?')}: missing {sorted(missing)}")
        check_id = str(entry["id"])
        if check_id in catalog:
            raise CatalogError(f"duplicate check id {check_id}")
        if entry["category"] not in CATEGORIES:
            raise CatalogError(f"{check_id}: unknown category {entry['category']!r}")
        if entry["severity"] not in SEVERITIES:
            raise CatalogError(f"{check_id}: unknown severity {entry['severity']!r}")
        catalog[check_id] = CheckInfo(
            id=check_id,
            category=cast(Category, entry["category"]),
            severity=cast(Severity, entry["severity"]),
            title=str(entry["title"]).strip(),
            why_it_matters=" ".join(str(entry["why_it_matters"]).split()),
            how_to_fix=" ".join(str(entry["how_to_fix"]).split()),
            applies_to=str(entry["applies_to"]),
        )
    return catalog


@lru_cache
def load_catalog() -> dict[str, CheckInfo]:
    return parse_catalog(CATALOG_PATH.read_text(encoding="utf-8"))
