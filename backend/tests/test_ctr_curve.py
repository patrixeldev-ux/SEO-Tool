"""CTR curve: isotonic fit, sparse-bucket borrowing, rounding, and the Wilson interval. Expected values are
worked out by hand in the comments."""

from __future__ import annotations

import math
import random
from itertools import pairwise

import pytest

from searchos.stats.brand import brand_terms, domain_label, is_branded
from searchos.stats.ctr_curve import (
    DEFAULT_CTR,
    MIN_IMPRESSIONS,
    POSITIONS,
    Bucket,
    PositionObs,
    bucket_of,
    bucketise,
    default_curve,
    fit_ctr_curve,
    isotonic_non_increasing,
    wilson_interval,
)


# ---- pool-adjacent-violators ------------------------------------------------------------------------
def test_pav_known_example_equal_weights() -> None:
    # 0.30 | 0.10 0.20 violate (0.10 < 0.20 is an increase) -> pooled mean 0.15 | 0.05 is fine.
    fit = isotonic_non_increasing([0.30, 0.10, 0.20, 0.05], [1, 1, 1, 1])
    assert list(fit) == pytest.approx([0.30, 0.15, 0.15, 0.05])


def test_pav_weights_are_respected() -> None:
    # (0.1*3 + 0.2*1) / 4 = 0.125
    fit = isotonic_non_increasing([0.3, 0.1, 0.2, 0.05], [1, 3, 1, 1])
    assert list(fit) == pytest.approx([0.3, 0.125, 0.125, 0.05])


def test_pav_cascading_merge() -> None:
    # 0.1 and 0.3 (weights 1, 2) pool to 0.7/3 = 0.2333 which now exceeds 0.2 -> everything pools:
    # (0.2*1 + 0.1*1 + 0.3*2) / 4 = 0.225
    fit = isotonic_non_increasing([0.2, 0.1, 0.3], [1, 1, 2])
    assert list(fit) == pytest.approx([0.225, 0.225, 0.225])


def test_pav_leaves_a_non_increasing_sequence_unchanged() -> None:
    values = [0.4, 0.3, 0.3, 0.1, 0.0]
    assert list(isotonic_non_increasing(values, [5, 1, 9, 2, 7])) == pytest.approx(values)


def test_pav_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="same length"):
        isotonic_non_increasing([1.0], [1.0, 2.0])
    with pytest.raises(ValueError, match="positive"):
        isotonic_non_increasing([1.0, 0.5], [1.0, 0.0])


def test_pav_output_is_monotone_and_preserves_the_weighted_mean_on_random_input() -> None:
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 20)
        values = [rng.random() for _ in range(n)]
        weights = [rng.uniform(0.1, 50) for _ in range(n)]
        fit = isotonic_non_increasing(values, weights)
        assert all(a >= b - 1e-12 for a, b in pairwise(fit))
        total = sum(weights)
        assert sum(v * w for v, w in zip(values, weights, strict=True)) / total == pytest.approx(
            sum(f * w for f, w in zip(fit, weights, strict=True)) / total
        )


# ---- bucketing --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("position", "bucket"),
    [
        (0.4, None),
        (0.5, 1),
        (1.0, 1),
        (1.49, 1),
        (1.5, 2),
        (2.5, 3),
        (20.49, 20),
        (20.5, None),
        (35.0, None),
        (math.nan, None),
    ],
)
def test_bucket_of_rounds_half_up(position: float, bucket: int | None) -> None:
    assert bucket_of(position) == bucket


def test_bucketise_sums_rows_per_rounded_position_and_caps_clicks() -> None:
    buckets = bucketise(
        [
            PositionObs(1.2, 100, 30),
            PositionObs(0.8, 50, 10),  # also position 1
            PositionObs(2.6, 40, 99),  # clicks cannot exceed impressions -> 40
            PositionObs(25.0, 1000, 5),  # outside the curve
            PositionObs(3.0, 0, 0),
        ]
    )
    assert buckets[0] == Bucket(150, 40)
    assert buckets[2] == Bucket(40, 40)
    assert sum(b.impressions for b in buckets) == 190


# ---- the fit ----------------------------------------------------------------------------------------
def test_default_curve_is_a_valid_prior() -> None:
    assert len(DEFAULT_CTR) == POSITIONS
    assert all(a >= b for a, b in pairwise(DEFAULT_CTR))
    assert all(0 < v < 1 for v in DEFAULT_CTR)
    curve = default_curve()
    assert curve.summary == "default" and curve.at(1) == DEFAULT_CTR[0]


def test_no_data_gives_the_default_curve() -> None:
    curve = fit_ctr_curve([])
    assert list(curve.values) == pytest.approx(list(DEFAULT_CTR))
    assert set(curve.confidence) == {"default"} and curve.summary == "default"


def test_output_is_always_non_increasing_even_for_noisy_data() -> None:
    rng = random.Random(3)
    obs = [
        PositionObs(float(p), 5000, int(5000 * rng.uniform(0.01, 0.4))) for p in range(1, 21)
    ]  # random CTRs per position: plenty of violations
    curve = fit_ctr_curve(obs)
    assert set(curve.confidence) == {"site"}
    assert all(a >= b - 1e-12 for a, b in pairwise(curve.values))


def test_inverted_buckets_are_pooled_with_impression_weights() -> None:
    # position 1: 600 impressions at 10%; position 2: 6000 impressions at 20% -> pooled (60 + 1200) / 6600
    curve = fit_ctr_curve([PositionObs(1, 600, 60), PositionObs(2, 6000, 1200)])
    expected = (60 + 1200) / 6600
    assert curve.values[0] == pytest.approx(expected) and curve.values[1] == pytest.approx(expected)
    assert curve.confidence[:2] == ("site", "site")
    assert curve.impressions[:2] == (600, 6000)


def test_buckets_below_the_threshold_borrow_the_default_curve() -> None:
    curve = fit_ctr_curve(
        [PositionObs(1, MIN_IMPRESSIONS, 150), PositionObs(2, MIN_IMPRESSIONS - 1, 400)]  # 499 is too few
    )
    assert curve.confidence[0] == "site" and curve.values[0] == pytest.approx(0.30)
    assert curve.confidence[1] == "default"
    assert curve.at(2) <= curve.at(1)
    assert curve.summary == "pooled"  # a mixture is labelled as such


def test_sparse_buckets_borrow_the_pooled_curve_when_it_has_enough_data() -> None:
    pooled = [Bucket(0, 0)] * POSITIONS
    pooled[1] = Bucket(10_000, 1_800)  # 18% at position 2
    pooled[2] = Bucket(100, 50)  # pooled has too little at position 3 -> default
    curve = fit_ctr_curve([PositionObs(1, 5000, 1500)], pooled=pooled)
    assert curve.confidence[0] == "site"
    assert curve.confidence[1] == "pooled" and curve.values[1] == pytest.approx(0.18)
    assert curve.confidence[2] == "default"
    assert curve.at(3) == pytest.approx(DEFAULT_CTR[2])


def test_pooled_must_cover_all_positions() -> None:
    with pytest.raises(ValueError, match="pooled"):
        fit_ctr_curve([], pooled=[Bucket(1, 1)])


def test_curve_lookup_clamps_and_rounds() -> None:
    curve = fit_ctr_curve([])
    assert curve.at(0.2) == curve.values[0]
    assert curve.at(7.5) == curve.values[7]  # half rounds up to 8
    assert curve.at(99) == curve.values[19]
    assert curve.confidence_at(99) == "default"


# ---- Wilson interval --------------------------------------------------------------------------------
def test_wilson_known_values() -> None:
    low, high = wilson_interval(50, 100)
    assert (low, high) == pytest.approx((0.4038, 0.5962), abs=1e-4)
    low, high = wilson_interval(0, 100)  # upper = z^2 / (n + z^2) = 3.8415 / 103.8415
    assert low == pytest.approx(0.0, abs=1e-12) and high == pytest.approx(0.036995, abs=1e-5)
    low, high = wilson_interval(100, 100)
    assert high == pytest.approx(1.0, abs=1e-12) and low == pytest.approx(1 - 0.036995, abs=1e-5)
    assert wilson_interval(0, 0) == (0.0, 1.0)


# ---- brand terms ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("domain", "label"),
    [
        ("example.com", "example"),
        ("shop.acme-widgets.co.uk", "acme-widgets"),
        ("acme.io", "acme"),
        ("localhost", "localhost"),
    ],
)
def test_domain_label(domain: str, label: str) -> None:
    assert domain_label(domain) == label


def test_brand_terms_and_matching() -> None:
    terms = brand_terms("acme-widgets.example", extra=["Acme Co", "  "])
    assert {"acme widgets", "acmewidgets", "acme co", "acmeco"} <= terms
    assert is_branded("Acme Widgets pricing", terms)
    assert is_branded("acmewidgets login", terms)
    assert is_branded("acme-widgets reviews", terms)
    assert is_branded("is acme co legit", terms)
    assert not is_branded("blue widgets for sale", terms)
    assert not is_branded("", terms)


def test_short_brand_terms_match_whole_words_only() -> None:
    terms = brand_terms("hp.com")
    assert is_branded("hp laptop", terms)
    assert not is_branded("whip cream recipe", terms)  # 'hp' inside a word
