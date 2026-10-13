"""Applying the page operations (deploy/ops.py) to an HTML string, keeping the rest of the file byte for byte.

Used by the GitHub deployer (static HTML files) and by the development edge simulator. The Cloudflare Worker and
the WordPress plugin implement the same operations with the same rules:

* every operation is idempotent: applying it twice gives the same page as applying it once;
* a link is inserted only by wrapping the first exact occurrence of its anchor text inside a text node of a
  paragraph-like element (`p li td dd blockquote figcaption`); never inside a link, script, style, heading,
  button, form control, code, `nav`, `header`, `footer` or `aside`. The text is matched as written in the
  source (entities are not decoded), so an anchor with `&` in it is not found and nothing is changed.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any
from urllib.parse import urljoin

FORBIDDEN = frozenset(
    "a script style h1 h2 h3 h4 h5 h6 button select textarea code pre nav header footer aside form option label".split()
)
ALLOWED = frozenset("p li td dd blockquote figcaption".split())
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

_TOKEN = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<![^>]*>|<\?.*?\?>"
    r"|<(script|style)\b[^>]*>.*?</\1\s*>"
    r"|</[a-zA-Z][^>]*>|<[a-zA-Z][^>]*>|[^<]+|<",
    re.S | re.I,
)
_TAG_NAME = re.compile(r"</?([a-zA-Z][a-zA-Z0-9:-]*)")


def _attr_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"""\s{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.I)


def get_attr(tag: str, name: str) -> str | None:
    m = _attr_pattern(name).search(tag)
    if not m:
        return None
    raw = next(g for g in m.groups() if g is not None)
    return html_lib.unescape(raw)


def set_attr(tag: str, name: str, value: str) -> str:
    """The start tag with attribute `name` set (replaced or added), quoted and escaped."""
    new = f'{name}="{html_lib.escape(value, quote=True)}"'
    pat = _attr_pattern(name)
    if pat.search(tag):
        return pat.sub(lambda m: f" {new}", tag, count=1)
    end = "/>" if tag.rstrip().endswith("/>") else ">"
    body = tag[: -len(end)].rstrip()
    return f"{body} {new}{' /' if end == '/>' else ''}>"


def _find_tags(source: str, name: str) -> list[re.Match[str]]:
    return list(re.finditer(rf"<{name}\b[^>]*>", source, re.I))


def _insert_in_head(source: str, snippet: str) -> str:
    m = re.search(r"</head\s*>", source, re.I)
    if m:
        return source[: m.start()] + snippet + "\n" + source[m.start() :]
    m = re.search(r"<head\b[^>]*>", source, re.I)
    if m:
        return source[: m.end()] + "\n" + snippet + source[m.end() :]
    m = re.search(r"<body\b[^>]*>", source, re.I)
    if m:
        return source[: m.start()] + snippet + "\n" + source[m.start() :]
    return snippet + "\n" + source


# ---- one operation each -------------------------------------------------------------------------------------
def set_title(source: str, value: str) -> str:
    pat = re.compile(r"(<title\b[^>]*>)(.*?)(</title\s*>)", re.I | re.S)
    escaped = html_lib.escape(value, quote=False)
    if pat.search(source):
        return pat.sub(lambda m: m.group(1) + escaped + m.group(3), source, count=1)
    return _insert_in_head(source, f"<title>{escaped}</title>")


def set_meta_description(source: str, value: str) -> str:
    for m in _find_tags(source, "meta"):
        if (get_attr(m.group(0), "name") or "").strip().lower() == "description":
            return source[: m.start()] + set_attr(m.group(0), "content", value) + source[m.end() :]
    return _insert_in_head(
        source, f'<meta name="description" content="{html_lib.escape(value, quote=True)}">'
    )


def set_canonical(source: str, value: str) -> str:
    for m in _find_tags(source, "link"):
        if "canonical" in (get_attr(m.group(0), "rel") or "").lower().split():
            return source[: m.start()] + set_attr(m.group(0), "href", value) + source[m.end() :]
    return _insert_in_head(source, f'<link rel="canonical" href="{html_lib.escape(value, quote=True)}">')


def set_alt(source: str, page_url: str, src: str, value: str) -> str:
    want = urljoin(page_url, src).rstrip("/")
    out: list[str] = []
    last = 0
    for m in _find_tags(source, "img"):
        have = urljoin(page_url, get_attr(m.group(0), "src") or "").rstrip("/")
        if have == want:
            out.append(source[last : m.start()])
            out.append(set_attr(m.group(0), "alt", value))
            last = m.end()
    out.append(source[last:])
    return "".join(out)


def _script_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _entity_types(data: Any) -> list[str]:
    items = data if isinstance(data, list) else [data]
    out: list[str] = []
    for item in items:
        nodes = item.get("@graph", [item]) if isinstance(item, dict) else []
        for node in nodes:
            if isinstance(node, dict):
                t = node.get("@type")
                out.extend(str(x).rsplit("/", 1)[-1] for x in (t if isinstance(t, list) else [t]) if x)
    return out


def set_jsonld(source: str, dom_id: str, value: dict[str, Any], replace: Any) -> str:
    block = re.compile(
        r'<script\b[^>]*type\s*=\s*["\']?application/ld\+json["\']?[^>]*>(.*?)</script\s*>', re.I | re.S
    )

    def drop(m: re.Match[str]) -> str:
        tag = m.group(0)
        if get_attr(tag[: tag.index(">") + 1], "id") == dom_id:
            return ""  # our own block from an earlier run: replaced below
        if replace is None:
            return tag
        try:
            data = json.loads(html_lib.unescape(m.group(1)))
        except ValueError:
            return "" if replace == "invalid" else tag
        if isinstance(replace, dict) and set(_entity_types(data)) & set(replace.get("types", [])):
            return ""
        return tag

    cleaned = block.sub(drop, source)
    snippet = f'<script type="application/ld+json" id="{dom_id}">{_script_json(value)}</script>'
    return _insert_in_head(cleaned, snippet)


def insert_link(source: str, op_id: str, anchor: str, to: str) -> str:
    if f'data-searchos="{op_id}"' in source:
        return source
    stack: list[str] = []
    for m in _TOKEN.finditer(source):
        tok = m.group(0)
        start = m.start()
        if tok.startswith("<!") or tok.startswith("<?") or m.group(1):
            continue
        if tok.startswith("</"):
            nm = _TAG_NAME.match(tok)
            if nm and nm.group(1).lower() in stack:
                name = nm.group(1).lower()
                while stack and stack.pop() != name:
                    pass
            continue
        if tok.startswith("<") and len(tok) > 1:
            nm = _TAG_NAME.match(tok)
            if nm:
                name = nm.group(1).lower()
                if name not in VOID and not tok.rstrip().endswith("/>"):
                    stack.append(name)
            continue
        if tok == "<" or not tok.strip():
            continue
        if any(n in FORBIDDEN for n in stack) or not any(n in ALLOWED for n in stack):
            continue
        idx = tok.find(anchor)
        if idx < 0:
            continue
        link = (
            f'<a href="{html_lib.escape(to, quote=True)}" data-searchos="{op_id}">'
            f"{html_lib.escape(anchor, quote=False)}</a>"
        )
        return source[: start + idx] + link + source[start + idx + len(anchor) :]
    return source


def apply_ops(source: str, ops: list[dict[str, Any]], page_url: str) -> str:
    for op in ops:
        kind = op["op"]
        if kind == "title":
            source = set_title(source, op["value"])
        elif kind == "meta_description":
            source = set_meta_description(source, op["value"])
        elif kind == "canonical":
            source = set_canonical(source, op["value"])
        elif kind == "alt":
            source = set_alt(source, page_url, op["src"], op["value"])
        elif kind == "jsonld":
            source = set_jsonld(source, op["id"], op["value"], op.get("replace"))
        elif kind == "link":
            source = insert_link(source, op["id"], op["anchor"], op["to"])
        else:
            raise ValueError(f"unknown operation {kind!r}")
    return source
