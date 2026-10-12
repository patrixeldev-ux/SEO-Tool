"""A provider for tests and for the development smoke test (`LLM_PROVIDER=fake`, refused in production).

By default it answers the planner's tasks with deterministic, plausible values built only from the data block in
the prompt, so everything downstream (validators, the plan board) can be exercised without a network or a key.
Tests can instead script it: pass `responses` (consumed in order; an item may be a dict or an exception) or a
`handler(task, data) -> dict`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from searchos.llm.base import LLMError, Usage

Handler = Callable[[str, dict[str, Any]], dict[str, Any]]
_TASK = re.compile(r"^TASK:\s*(\S+)", re.M)
_DATA = re.compile(r"<<<DATA (?P<nonce>[0-9a-f]+)\n(?P<body>.*?)\n(?P=nonce)>>>", re.S)

_STOP = {"the", "a", "an", "and", "of", "for", "to", "in", "on", "with", "your", "our", "at", "by"}


def parse_prompt(user: str) -> tuple[str, dict[str, Any]]:
    """The task name and the JSON data block of a planner prompt."""
    task = _TASK.search(user)
    data = _DATA.search(user)
    if not task or not data:
        raise LLMError("fake provider: prompt has no TASK line or DATA block")
    return task.group(1), json.loads(data.group("body"))


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'&-]*", text)


def _cut(text: str, limit: int) -> str:
    """Longest prefix of whole words that fits in `limit` characters."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-|&")


def _title_case(text: str) -> str:
    return " ".join(w if w.isupper() and len(w) > 1 else w[:1].upper() + w[1:] for w in text.split())


def fake_title(data: dict[str, Any]) -> str:
    base = data.get("h1") or data.get("current") or data.get("slug") or data["page_title"] or "Our page"
    query = (data.get("target_query") or "").strip()
    brand = data.get("brand") or ""
    parts = [str(base).strip()]
    if query and query.lower() not in parts[0].lower():
        parts.append(query)
    text = _title_case(" - ".join(parts))
    if brand and brand.lower() not in text.lower() and len(text) + len(brand) + 3 <= 60:
        text = f"{text} | {brand}"
    seed = data.get("slug") or ""
    while len(text) < 30:
        extra = _title_case(" ".join(w for w in _words(seed) if w.lower() not in _STOP)) or "Guide"
        text = f"{text} - {extra}" if extra.lower() not in text.lower() else f"{text} - Guide and Tips"
        if len(text) < 30:
            text = f"{text} for Everyone"
    return _cut(text, 60)


def fake_meta(data: dict[str, Any]) -> str:
    excerpt = " ".join(str(data.get("excerpt") or "").split())
    first = re.split(r"(?<=[.!?])\s", excerpt)[0] if excerpt else ""
    query = (data.get("target_query") or "").strip()
    pieces = [first] if first else [str(data.get("h1") or data.get("page_title") or "This page")]
    if query:
        pieces.append(f"Find out more about {query}.")
    tail = f"Read more on {data.get('brand') or data.get('domain') or 'our site'}."
    text = " ".join(pieces)
    while len(text) < 120:
        text = (
            f"{text} {tail}"
            if tail not in text
            else f"{text} Clear answers, plain language and practical tips."
        )
        if len(text) < 120:
            text = f"{text} Everything you need to know in one place."
    return _cut(text, 155)


def fake_alt(data: dict[str, Any]) -> str:
    name = re.sub(r"\.[A-Za-z0-9]+$", "", str(data.get("filename") or ""))
    words = [w for w in re.split(r"[-_\s]+", name) if w and not w.isdigit()]
    subject = " ".join(words).strip() or "Illustration"
    page = str(data.get("page_title") or "").strip()
    text = f"{subject.capitalize()} shown on the page {page}" if page else subject.capitalize()
    if text.lower() == str(data.get("filename") or "").lower():
        text = f"{text} (picture)"
    return _cut(text, 120)


def fake_jsonld(data: dict[str, Any]) -> dict[str, Any]:
    kind = data["schema_type"]
    facts = data.get("facts", {})
    existing = dict(data.get("existing") or {})
    entity: dict[str, Any] = {"@context": "https://schema.org", "@type": existing.get("@type", kind)}
    entity.update({k: v for k, v in existing.items() if k not in ("@context", "@type")})
    name = facts.get("name")
    url = facts.get("url")
    if kind in ("Organization", "LocalBusiness"):
        if name:
            entity.setdefault("name", name)
        if url:
            entity.setdefault("url", url)
    elif kind == "Article":
        if facts.get("headline"):
            entity.setdefault("headline", facts["headline"])
        if facts.get("author"):
            entity.setdefault("author", {"@type": "Person", "name": facts["author"]})
        if facts.get("date_published"):
            entity.setdefault("datePublished", facts["date_published"])
    elif kind == "WebPage":
        if facts.get("headline"):
            entity.setdefault("name", facts["headline"])
        if url:
            entity.setdefault("url", url)
    return {"jsonld": json.dumps(entity, ensure_ascii=False)}


def default_handler(task: str, data: dict[str, Any]) -> dict[str, Any]:
    if task == "rewrite_title":
        return {"title": fake_title(data)}
    if task == "rewrite_meta":
        return {"description": fake_meta(data)}
    if task == "add_alt":
        return {"alt": fake_alt(data)}
    if task in ("add_jsonld", "fix_jsonld"):
        return fake_jsonld(data)
    raise LLMError(f"fake provider: unknown task {task!r}")


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        responses: list[dict[str, Any] | Exception] | None = None,
        handler: Handler | None = None,
        model: str = "fake-1",
        tokens: tuple[int, int] = (200, 40),
    ) -> None:
        self.model = model
        self.last_usage = Usage()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []  # (system, user, schema)
        self._responses = list(responses) if responses is not None else None
        self._handler = handler or default_handler
        self._tokens = tokens

    async def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        self.last_usage = Usage(*self._tokens)
        if self._responses is not None:
            if not self._responses:
                raise LLMError("fake provider: no scripted response left")
            item = self._responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        task, data = parse_prompt(user)
        return self._handler(task, data)
