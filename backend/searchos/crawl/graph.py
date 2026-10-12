"""Link-graph facts computed once the crawl is finished: click depth and inlink counts.

Pure function, no I/O. A redirecting URL counts as the page it redirects to: a link to /old (301 to /new)
is a link to /new for depth and for inlinks.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field


@dataclass(slots=True)
class GraphResult:
    depth: dict[str, int] = field(default_factory=dict)  # URL -> clicks from the start page
    inlinks: dict[str, int] = field(default_factory=dict)  # URL -> distinct internal pages linking to it


def resolve_redirect(url: str, redirects: Mapping[str, str], max_hops: int = 10) -> str:
    seen = 0
    while url in redirects and seen < max_hops:
        url = redirects[url]
        seen += 1
    return url


def compute_graph(
    start_url: str,
    edges: Mapping[str, Collection[str]],
    redirects: Mapping[str, str],
    page_urls: Collection[str],
) -> GraphResult:
    """`edges[page]` = internal URLs linked from `page`; `redirects[src]` = URL `src` redirects to."""
    resolve = lambda u: resolve_redirect(u, redirects)  # noqa: E731
    result = GraphResult()

    start = resolve(start_url)
    depth: dict[str, int] = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for target in edges.get(current, ()):
            nxt = resolve(target)
            if nxt not in depth:
                depth[nxt] = depth[current] + 1
                queue.append(nxt)

    linkers_direct: dict[str, set[str]] = {}
    for source, targets in edges.items():
        for target in targets:
            if target != source:
                linkers_direct.setdefault(target, set()).add(source)

    # A URL's inlinks are the direct ones plus those aimed at any URL that redirects to it.
    via_redirect: dict[str, set[str]] = {}
    for src in redirects:
        final = resolve(src)
        via_redirect.setdefault(final, set()).update(linkers_direct.get(src, set()))

    for url in page_urls:
        final = resolve(url)
        result.depth[url] = depth.get(final, -1)
        linkers = set(linkers_direct.get(url, set()))
        if url == final:
            linkers |= via_redirect.get(url, set())
        linkers.discard(url)
        result.inlinks[url] = len(linkers)
    result.depth = {u: d for u, d in result.depth.items() if d >= 0}
    return result
