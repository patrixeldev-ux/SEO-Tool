"""Click-through rate as a function of search position, fitted from a site's own Search Console data.

Method
------
1. Bucket observations by rounded position 1..20 (half rounds up). Callers pass only the rows that belong in the
   curve: last 90 days, no conversational queries, no branded queries (see `searchos.stats.brand`).
2. Per bucket: CTR = clicks / impressions.
3. Buckets with fewer than `MIN_IMPRESSIONS` (500) impressions are too noisy to trust. They borrow a value from
   a pooled curve (other sites of the same organisation) when it has enough data for that position, otherwise
   from `DEFAULT_CTR`, a fixed industry prior (low confidence).
4. Enforce "a better position never has a lower CTR" with weighted isotonic regression (pool-adjacent-violators,
   non-increasing), weights = impressions (borrowed buckets weigh `MIN_IMPRESSIONS`).

Each position reports where its value came from: `site`, `pooled` or `default`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

Confidence = Literal["site", "pooled", "default"]

POSITIONS = 20
MIN_IMPRESSIONS = 500

# Industry prior used only where a site has too little data of its own AND no pooled curve exists.
# Source: a rounded blend of published organic-CTR-by-position studies (Backlinko 2023 and Advanced Web Ranking
# 2023 report CTRs of roughly this shape: ~28-32% at position 1, ~15-25% at 2, ~10-11% at 3, ~2% by 10).
# These are NOT measured by us and vary a lot by industry, device and SERP features: "industry prior, low
# confidence". Non-increasing by construction (a test checks it).
DEFAULT_CTR: tuple[float, ...] = (
    0.285, 0.157, 0.110, 0.080, 0.062, 0.048, 0.038, 0.031, 0.026, 0.022,
    0.012, 0.010, 0.0085, 0.0075, 0.0065, 0.0058, 0.0052, 0.0047, 0.0043, 0.0040,
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class PositionObs:
    """Impressions and clicks observed at an average position (one Search Console row, or a group of rows)."""

    position: float
    impressions: int
    clicks: int


@dataclass(frozen=True, slots=True)
class CtrCurve:
    """CTR for positions 1..20 (index 0 is position 1)."""

    values: tuple[float, ...]
    confidence: tuple[Confidence, ...]
    impressions: tuple[int, ...]  # the site's own impressions per position (what the fit saw)

    def at(self, position: float) -> float:
        """Expected CTR at an average position; positions outside 1..20 clamp to the nearest end."""
        return self.values[_clamped_index(position)]

    def confidence_at(self, position: float) -> Confidence:
        return self.confidence[_clamped_index(position)]

    @property
    def summary(self) -> Confidence:
        """'site' only when every position rests on the site's own data."""
        if all(c == "site" for c in self.confidence):
            return "site"
        return "default" if all(c == "default" for c in self.confidence) else "pooled"


def bucket_of(position: float) -> int | None:
    """Rounded position 1..20, or None when the position falls outside the curve (half rounds up)."""
    if not math.isfinite(position):
        return None
    bucket = math.floor(position + 0.5)
    return bucket if 1 <= bucket <= POSITIONS else None


def _clamped_index(position: float) -> int:
    if not math.isfinite(position):
        return POSITIONS - 1
    return min(max(math.floor(position + 0.5), 1), POSITIONS) - 1


def isotonic_non_increasing(values: Sequence[float], weights: Sequence[float]) -> npt.NDArray[np.float64]:
    """Weighted least-squares fit of a non-increasing sequence (pool-adjacent-violators).

    Walk left to right keeping blocks of (weighted mean, total weight, length). A new element starts as its own
    block; while the previous block's mean is LOWER than the last block's (a violation of "non-increasing"),
    merge the two into their weighted mean. Weights must be positive.
    """
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    if any(w <= 0 for w in weights):
        raise ValueError("weights must be positive")
    means: list[float] = []
    totals: list[float] = []
    sizes: list[int] = []
    for value, weight in zip(values, weights, strict=True):
        means.append(float(value))
        totals.append(float(weight))
        sizes.append(1)
        while len(means) >= 2 and means[-2] < means[-1]:
            weight_sum = totals[-2] + totals[-1]
            means[-2] = (means[-2] * totals[-2] + means[-1] * totals[-1]) / weight_sum
            totals[-2] = weight_sum
            sizes[-2] += sizes[-1]
            del means[-1], totals[-1], sizes[-1]
    return np.repeat(np.array(means, dtype=np.float64), np.array(sizes, dtype=np.int64))


@dataclass(frozen=True, slots=True)
class Bucket:
    impressions: int = 0
    clicks: int = 0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0


def bucketise(observations: Sequence[PositionObs]) -> list[Bucket]:
    impressions = [0] * POSITIONS
    clicks = [0] * POSITIONS
    for obs in observations:
        bucket = bucket_of(obs.position)
        if bucket is None or obs.impressions <= 0:
            continue
        impressions[bucket - 1] += obs.impressions
        clicks[bucket - 1] += min(obs.clicks, obs.impressions)
    return [Bucket(i, c) for i, c in zip(impressions, clicks, strict=True)]


def fit_ctr_curve(
    observations: Sequence[PositionObs],
    *,
    pooled: Sequence[Bucket] | None = None,
    min_impressions: int = MIN_IMPRESSIONS,
) -> CtrCurve:
    """Fit the curve. `pooled` is the same 20 buckets summed over other sites (or None)."""
    own = bucketise(observations)
    if pooled is not None and len(pooled) != POSITIONS:
        raise ValueError(f"pooled must have {POSITIONS} buckets")
    raw: list[float] = []
    weight: list[float] = []
    source: list[Confidence] = []
    for index, bucket in enumerate(own):
        if bucket.impressions >= min_impressions:
            raw.append(bucket.ctr)
            weight.append(float(bucket.impressions))
            source.append("site")
        elif pooled is not None and pooled[index].impressions >= min_impressions:
            raw.append(pooled[index].ctr)
            weight.append(float(min_impressions))
            source.append("pooled")
        else:
            raw.append(DEFAULT_CTR[index])
            weight.append(float(min_impressions))
            source.append("default")
    fitted = isotonic_non_increasing(raw, weight)
    return CtrCurve(
        values=tuple(float(v) for v in fitted),
        confidence=tuple(source),
        impressions=tuple(b.impressions for b in own),
    )


def default_curve() -> CtrCurve:
    sources: list[Confidence] = ["default"] * POSITIONS
    return CtrCurve(DEFAULT_CTR, tuple(sources), (0,) * POSITIONS)


def wilson_interval(clicks: int, impressions: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for a proportion (95% by default). (0, 1) when there are no impressions."""
    if impressions <= 0:
        return 0.0, 1.0
    p = min(max(clicks / impressions, 0.0), 1.0)
    z2 = z * z
    denom = 1 + z2 / impressions
    centre = (p + z2 / (2 * impressions)) / denom
    half = z * math.sqrt(p * (1 - p) / impressions + z2 / (4 * impressions * impressions)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)
