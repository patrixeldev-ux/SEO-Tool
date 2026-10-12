"""Hard validators for everything the AI (or a person editing) proposes. Pure functions, no I/O.

Each returns a list of plain-language problems; an empty list means the value may be used. Nothing the AI produces is
ever used without passing these: they are the only thing between a model's answer and a deployed page.

Rules (plan Decisions, "Validators"):

* title - 30..60 characters, not ALL CAPS, no keyword stuffing (no word of 3+ letters more than twice), unique across
  the site's current titles and the other proposals, and different from the page's current title.
* meta description - 120..155 characters, same ALL-CAPS rule, unique across the site.
* image alt text - 5..125 characters, must not start with "image of" / "picture of", must not be the file name.
* JSON-LD - parses; ``@context`` is schema.org; the type is one we support; every property our own table
  (`audit/schema_rules.py`) requires is present; no reviews or ratings are ever added; and no value that is new
  compared with the existing markup may be missing from the page's own text (nothing is invented).
* internal link - the target is an indexable page that answers 200 and is not the page itself; the page does not
  already link to it; the anchor text is 2..8 words and appears word for word in the page's text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from searchos.audit.schema_rules import (
    ARTICLE_FAMILY,
    ORGANIZATION_TYPES,
    RULES,
    TypeRule,
    is_present,
    rule_family,
    types_of,
)

TITLE_MIN, TITLE_MAX = 30, 60
META_MIN, META_MAX = 120, 155
ALT_MIN, ALT_MAX = 5, 125
ANCHOR_MIN_WORDS, ANCHOR_MAX_WORDS = 2, 8
MAX_REPEATS = 2  # a word of 3+ letters may appear at most this often in a title

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_TAGS = re.compile(r"<[^>]*>|[<>]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BAD_ALT_START = re.compile(r"^\s*(image|picture)\s+of\b", re.I)

# Types we are willing to generate (the audit's required-property table plus WebPage for pages with broken markup).
SUPPORTED_TYPES: frozenset[str] = frozenset(
    set(ORGANIZATION_TYPES) | set(ARTICLE_FAMILY) | {"Product", "FAQPage", "BreadcrumbList", "WebPage"}
)
EXTRA_RULES: dict[str, TypeRule] = {"WebPage": TypeRule(required=("name", "url"))}
# Never produced by the AI: they would be invented. Allowed to stay if the page's existing markup already has them.
NEVER_ADDED_KEYS: frozenset[str] = frozenset(
    {
        "aggregateRating",
        "review",
        "reviews",
        "ratingValue",
        "reviewCount",
        "ratingCount",
        "bestRating",
        "worstRating",
    }
)
_STRUCTURAL_KEYS = frozenset({"@context", "@type", "@id"})
_SCHEMA_CONTEXT = re.compile(r"^https?://schema\.org/?$", re.I)


def squash(text: str) -> str:
    """Collapse whitespace; the form in which text is compared and stored."""
    return _WS.sub(" ", text).strip()


def norm_key(text: str) -> str:
    """The form in which two titles/descriptions are compared for uniqueness."""
    return squash(text).lower()


def _letters(text: str) -> str:
    return "".join(ch for ch in text if ch.isalpha())


def _shouting(text: str) -> bool:
    """ALL CAPS: every letter is upper case (4+ letters), or 3+ words of 4+ letters are written in capitals."""
    letters = _letters(text)
    if len(letters) >= 4 and letters.isupper():
        return True
    loud = [w for w in _WORD.findall(text) if len(w) >= 4 and w.isalpha() and w.isupper()]
    return len(loud) >= 3


def _stuffed(text: str) -> str | None:
    counts: dict[str, int] = {}
    for word in _WORD.findall(text.lower()):
        if len(word) >= 3:
            counts[word] = counts.get(word, 0) + 1
    for word, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n > MAX_REPEATS:
            return word
    return None


def _text_problems(text: str, label: str) -> list[str]:
    problems: list[str] = []
    if _TAGS.search(text):
        problems.append(f"The {label} contains HTML.")
    if _CONTROL.search(text) or "\n" in text or "\r" in text:
        problems.append(f"The {label} must be a single line of plain text.")
    return problems


def validate_title(title: object, *, taken: Collection[str] = (), current: str | None = None) -> list[str]:
    """`taken` holds `norm_key` forms of the titles of all OTHER pages and of other proposals."""
    if not isinstance(title, str) or not title.strip():
        return ["The title is empty."]
    problems = _text_problems(title, "title")
    text = squash(title)
    n = len(text)
    if n < TITLE_MIN:
        problems.append(f"The title is {n} characters; it must be at least {TITLE_MIN}.")
    if n > TITLE_MAX:
        problems.append(f"The title is {n} characters; it must be at most {TITLE_MAX}.")
    if _shouting(text):
        problems.append("The title is written in capital letters; use normal capitalisation.")
    word = _stuffed(text)
    if word:
        problems.append(
            f'The word "{word}" appears more than {MAX_REPEATS} times; that looks like keyword stuffing.'
        )
    if current is not None and norm_key(current) == norm_key(text):
        problems.append("The title is the same as the current one.")
    elif norm_key(text) in taken:
        problems.append(
            "Another page (or another proposal) already uses this title; every title must be unique."
        )
    return problems


def validate_meta(
    description: object, *, taken: Collection[str] = (), current: str | None = None
) -> list[str]:
    if not isinstance(description, str) or not description.strip():
        return ["The description is empty."]
    problems = _text_problems(description, "description")
    text = squash(description)
    n = len(text)
    if n < META_MIN:
        problems.append(f"The description is {n} characters; it must be at least {META_MIN}.")
    if n > META_MAX:
        problems.append(f"The description is {n} characters; it must be at most {META_MAX}.")
    if _shouting(text):
        problems.append("The description is written in capital letters; use normal capitalisation.")
    if current is not None and norm_key(current) == norm_key(text):
        problems.append("The description is the same as the current one.")
    elif norm_key(text) in taken:
        problems.append(
            "Another page (or another proposal) already uses this description; each must be unique."
        )
    return problems


def _filename_forms(filename: str) -> set[str]:
    base = filename.rsplit("/", 1)[-1].lower()
    stem = re.sub(r"\.[a-z0-9]{2,5}$", "", base)
    return {base, stem, stem.replace("-", " ").replace("_", " ")}


def validate_alt(alt: object, *, filename: str = "") -> list[str]:
    if not isinstance(alt, str) or not alt.strip():
        return ["The alt text is empty."]
    problems = _text_problems(alt, "alt text")
    text = squash(alt)
    n = len(text)
    if n < ALT_MIN:
        problems.append(f"The alt text is {n} characters; it must be at least {ALT_MIN}.")
    if n > ALT_MAX:
        problems.append(f"The alt text is {n} characters; it must be at most {ALT_MAX}.")
    if _BAD_ALT_START.match(text):
        problems.append(
            'Alt text should not start with "image of" or "picture of"; screen readers already say it is an image.'
        )
    if filename and text.lower() in _filename_forms(filename):
        problems.append("The alt text is just the file name; describe what the image shows.")
    return problems


# ---- JSON-LD -----------------------------------------------------------------------------------------
def parse_jsonld(raw: object) -> tuple[dict[str, Any] | None, list[str]]:
    """Accepts a JSON text or an already-parsed object; returns (entity, problems)."""
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError as exc:
            return None, [f"The JSON-LD is not valid JSON ({exc.__class__.__name__}: {str(exc)[:80]})."]
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        return None, ["The JSON-LD must be a single JSON object."]
    return value, []


def _context_ok(context: Any) -> bool:
    if isinstance(context, str):
        return bool(_SCHEMA_CONTEXT.match(context.strip()))
    if isinstance(context, list):
        return any(isinstance(c, str) and _SCHEMA_CONTEXT.match(c.strip()) for c in context)
    return False


def _leaves(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    """(property path, scalar) for every scalar in a JSON value, skipping structural keys."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in _STRUCTURAL_KEYS:
                continue
            yield from _leaves(inner, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item, path)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield path, value


def _new_leaves(proposed: dict[str, Any], existing: dict[str, Any] | None) -> list[tuple[str, Any]]:
    old = {(p, str(v)) for p, v in _leaves(existing)} if existing else set()
    return [(p, v) for p, v in _leaves(proposed) if (p, str(v)) not in old]


def _grounded(value: Any, ground: str, hosts: Collection[str]) -> bool:
    if isinstance(value, (int, float)):
        forms = {str(value)}
        if isinstance(value, float) and value == int(value):
            forms.add(str(int(value)))
        return any(re.search(rf"(?<![\d.,]){re.escape(f)}(?!\d)", ground) for f in forms)
    text = squash(str(value)).lower()
    if not text:
        return True
    if text.startswith(("http://", "https://")):
        host = urlsplit(text).netloc.lower()
        if host in hosts or host.removeprefix("www.") in hosts:
            return True
    return text in ground


def _missing(entity: dict[str, Any], rule: TypeRule) -> list[str]:
    missing = [p for p in rule.required if not is_present(entity.get(p))]
    for group in rule.one_of:
        if not any(is_present(entity.get(p)) for p in group):
            missing.append(" or ".join(group))
    return missing


def validate_jsonld(
    raw: object,
    *,
    expected_type: str,
    ground: Iterable[str],
    hosts: Collection[str] = (),
    existing: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Returns (entity, problems). ``ground`` is every text the page legitimately contains: its main text, title,
    headings, meta description, plus facts the planner extracted from them (dates, an author, the site's own name)."""
    entity, problems = parse_jsonld(raw)
    if entity is None:
        return None, problems
    if not _context_ok(entity.get("@context")):
        problems.append('"@context" must be "https://schema.org".')
    types = types_of(entity)
    if not types:
        problems.append('"@type" is missing.')
    unsupported = [t for t in types if t not in SUPPORTED_TYPES]
    if unsupported:
        problems.append(
            f'The type "{unsupported[0]}" is not one we generate (supported: Organization, LocalBusiness, Article, Product, FAQPage, BreadcrumbList, WebPage).'
        )
    expected_family = rule_family(expected_type) or expected_type
    if types and not any((rule_family(t) or t) == expected_family for t in types):
        problems.append(f"The type must be {expected_type}, not {', '.join(types)}.")
    if existing is not None and types_of(existing) and set(types_of(existing)) != set(types):
        problems.append("The type of existing markup must not be changed.")

    for t in types:
        if t not in SUPPORTED_TYPES:
            continue
        family = rule_family(t)
        rule = RULES.get(family) if family else EXTRA_RULES.get(t)
        if rule is None:
            continue
        missing = _missing(entity, rule)
        if missing:
            problems.append(f"Required properties are missing for {t}: {', '.join(missing)}.")

    added = _new_leaves(entity, existing)
    old_keys = set(existing or {})
    for key in NEVER_ADDED_KEYS:
        if key in entity and key not in old_keys:
            problems.append(
                f'"{key}" is never generated: ratings and reviews must come from real reviews on the page.'
            )
    ground_text = squash(" \n ".join(ground)).lower()
    hosts_l = {h.lower() for h in hosts}
    for path, value in added:
        if not _grounded(value, ground_text, hosts_l):
            shown = squash(str(value))[:60]
            problems.append(
                f'The value "{shown}" for "{path}" does not appear on the page; values must not be invented.'
            )
    return entity, list(dict.fromkeys(problems))


# ---- internal links ------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LinkTarget:
    url: str
    status_code: int | None
    is_indexable: bool
    redirects: bool = False


def anchor_words(anchor: str) -> list[str]:
    return _WORD.findall(anchor)


def validate_internal_link(
    *,
    source_url: str,
    source_text: str,
    target: LinkTarget | None,
    already_linked: Collection[str],
    anchor: object,
) -> list[str]:
    problems: list[str] = []
    if target is None:
        return ["The target page was not found in the latest crawl."]
    if target.url == source_url:
        problems.append("A page cannot link to itself.")
    if not target.is_indexable or target.status_code != 200 or target.redirects:
        problems.append("The target must be an indexable page that answers 200 without redirecting.")
    if target.url in already_linked:
        problems.append("The page already links to the target.")
    if not isinstance(anchor, str) or not anchor.strip():
        problems.append("The anchor text is empty.")
        return problems
    text = squash(anchor)
    n = len(anchor_words(text))
    if n < ANCHOR_MIN_WORDS or n > ANCHOR_MAX_WORDS:
        problems.append(
            f"The anchor text has {n} word(s); it must have {ANCHOR_MIN_WORDS} to {ANCHOR_MAX_WORDS}."
        )
    if text.lower() not in squash(source_text).lower():
        problems.append("The anchor text does not appear word for word in the page's text.")
    return problems
