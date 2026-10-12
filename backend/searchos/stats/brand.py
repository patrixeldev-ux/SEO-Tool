"""Branded-query detection: queries that contain the site's own name are navigational, not discovery."""

from __future__ import annotations

import re
from collections.abc import Iterable

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "ac", "edu", "or", "ne"})
SHORT_TERM = 4  # terms shorter than this must match a whole word ("hp"), longer ones may be part of a word


def domain_label(domain: str) -> str:
    """The registrable name of a domain: 'shop.acme-widgets.co.uk' -> 'acme-widgets'. Heuristic (no suffix list)."""
    labels = [part for part in domain.lower().strip(".").split(".") if part]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return labels[-3]
    if len(labels) >= 2:
        return labels[-2]
    return labels[0] if labels else ""


def brand_terms(domain: str, extra: Iterable[str] = ()) -> frozenset[str]:
    """Normalised brand terms: the domain label ('acme-widgets' -> 'acme widgets', 'acmewidgets') plus any
    owner-supplied terms (site setting `brand_terms`). Each term is lowercase letters and digits, single-spaced."""
    raw = [domain_label(domain), *extra]
    terms: set[str] = set()
    for item in raw:
        spaced = " ".join(_NON_ALNUM.sub(" ", item.lower()).split())
        if spaced:
            terms.add(spaced)
            terms.add(spaced.replace(" ", ""))
    return frozenset(t for t in terms if t)


def is_branded(query: str, terms: frozenset[str]) -> bool:
    spaced = " ".join(_NON_ALNUM.sub(" ", query.lower()).split())
    if not spaced:
        return False
    squashed = spaced.replace(" ", "")
    padded = f" {spaced} "
    for term in terms:
        if len(term.replace(" ", "")) < SHORT_TERM:
            if f" {term} " in padded:
                return True
        elif term in spaced or term.replace(" ", "") in squashed:
            return True
    return False
