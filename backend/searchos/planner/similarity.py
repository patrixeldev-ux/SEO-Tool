"""Topical overlap between pages: TF-IDF cosine similarity of their text, computed locally and deterministically.

No embeddings and no API: the same pages always give the same numbers. Terms are lower-cased words of 3+ letters
without English stop words; term frequency is sub-linear (1 + ln tf); inverse document frequency is the smoothed
``ln((1 + N) / (1 + df)) + 1``; vectors are L2-normalised, so a dot product is a cosine in [0, 1].

`pick_anchor` finds, inside a source page's own text, the phrase that best names a target page: the anchor text of a
suggested internal link. The phrase is always copied word for word from the source text.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy import sparse

_STOP_TEXT = """a about above after again all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her here hers him
his how i if in into is it its just me more most my no nor not now of off on once only or other our out over own same
she should so some such than that the their them then there these they this those through to too under until up very
was we were what when where which while who whom why will with would you your yours get got make made one two new use
used using"""
STOP_WORDS: frozenset[str] = frozenset(_STOP_TEXT.split())
_TOKEN = re.compile(r"[^\W\d_][^\W_]{2,}", re.UNICODE)
_WORD_SPAN = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*", re.UNICODE)

# Similarity needed before a page is suggested as a link source. Calibrated on a synthetic two-topic page set (tests/data/topical_pages.py; see
# docs/METHODOLOGY.md, "Action priority" -> "Internal-link similarity threshold").
SIMILARITY_THRESHOLD = 0.05


def tokens(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in _TOKEN.finditer(text)) if t not in STOP_WORDS]


@dataclass(slots=True)
class TextIndex:
    urls: list[str]
    vocabulary: dict[str, int]
    matrix: sparse.csr_matrix  # rows: documents (L2 normalised), columns: terms
    row_of: dict[str, int]

    @classmethod
    def build(cls, documents: Mapping[str, str]) -> TextIndex:
        urls = sorted(documents)
        counts = [Counter(tokens(documents[u])) for u in urls]
        vocabulary = {t: i for i, t in enumerate(sorted({t for c in counts for t in c}))}
        n = len(urls)
        df = np.zeros(len(vocabulary), dtype=np.float64)
        for c in counts:
            for t in c:
                df[vocabulary[t]] += 1
        idf = np.log((1.0 + n) / (1.0 + df)) + 1.0
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []
        for r, c in enumerate(counts):
            for t, tf in c.items():
                j = vocabulary[t]
                rows.append(r)
                cols.append(j)
                vals.append((1.0 + math.log(tf)) * idf[j])
        matrix = sparse.csr_matrix((vals, (rows, cols)), shape=(n, len(vocabulary)), dtype=np.float64)
        norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
        norms[norms == 0] = 1.0
        matrix = sparse.diags(1.0 / norms) @ matrix
        return cls(urls, vocabulary, sparse.csr_matrix(matrix), {u: i for i, u in enumerate(urls)})

    def similarity_to(self, url: str) -> dict[str, float]:
        """Cosine similarity of every other document to `url`."""
        if url not in self.row_of:
            return {}
        row = self.matrix[self.row_of[url]]
        scores: npt.NDArray[np.float64] = np.asarray((self.matrix @ row.T).todense()).ravel()
        return {u: float(scores[i]) for i, u in enumerate(self.urls) if u != url}

    def top_terms(self, url: str, k: int = 8) -> list[str]:
        if url not in self.row_of:
            return []
        row = self.matrix[self.row_of[url]].tocoo()
        inverse = {j: t for t, j in self.vocabulary.items()}
        ranked = sorted(zip(row.data, row.col, strict=True), key=lambda p: (-p[0], inverse[p[1]]))
        return [inverse[j] for _v, j in ranked[:k]]


def ranked_sources(
    scores: Mapping[str, float], allowed: Iterable[str], threshold: float
) -> list[tuple[str, float]]:
    """Candidate source pages above the threshold, best first (ties broken by URL so the result is stable)."""
    allowed_set = set(allowed)
    return sorted(
        ((u, s) for u, s in scores.items() if u in allowed_set and s >= threshold),
        key=lambda p: (-p[1], p[0]),
    )


# ---- anchors -------------------------------------------------------------------------------------------
def pick_anchor(
    source_text: str, target_terms: Mapping[str, float], *, min_words: int = 2, max_words: int = 4
) -> str | None:
    """The phrase of 2..4 consecutive words in `source_text` that best matches the target's terms.

    `target_terms` maps lower-case words to a weight (the words of the target's title, headings and top query count
    more than its general vocabulary). A phrase must not start or end with a stop word, must not span punctuation,
    must contain a matching word and at least two content words. Best score wins; ties go to the earlier phrase.
    """
    spans = [(m.group(0), m.start(), m.end()) for m in _WORD_SPAN.finditer(source_text)]
    best: tuple[float, int, str] | None = None
    for i in range(len(spans)):
        for size in range(min_words, max_words + 1):
            j = i + size
            if j > len(spans):
                break
            window = spans[i:j]
            if any(
                re.search(r"[^\s]", source_text[window[k][2] : window[k + 1][1]]) for k in range(size - 1)
            ):
                break  # punctuation (or anything but whitespace) between two words: the phrase ends here
            words = [w[0].lower() for w in window]
            if words[0] in STOP_WORDS or words[-1] in STOP_WORDS:
                continue
            content = [w for w in words if w not in STOP_WORDS and len(w) >= 3]
            if len(content) < 2:
                continue
            hits = [target_terms.get(w, 0.0) for w in set(content)]
            score = sum(hits) - 0.05 * size
            if sum(1 for h in hits if h > 0) < 1:
                continue
            candidate = (score, -i, source_text[window[0][1] : window[-1][2]])
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    return best[2] if best else None


def term_weights(primary: Sequence[str], general: Sequence[str]) -> dict[str, float]:
    """Weights for `pick_anchor`: words from the title/headings/query count 2, other characteristic words 1."""
    weights: dict[str, float] = {}
    for word in general:
        weights[word] = 1.0
    for text in primary:
        for word in tokens(text):
            weights[word] = 2.0
    return weights
