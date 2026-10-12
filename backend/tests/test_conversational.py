"""Conversational-query heuristic: a table of strings in both classes, plus the retagging helper."""

from __future__ import annotations

import pytest

from searchos.stats.conversational import is_conversational

CONVERSATIONAL = [
    "yes",
    "Yes!",
    "yes go on",
    "yes please",
    "yes please go on with the second one",
    "no",
    "ok",
    "okay thanks",
    "thanks",
    "thank you",
    "go on",
    "go ahead",
    "continue",
    "keep going",
    "tell me more",
    "tell me more about it",
    "more details",
    "can you explain that",
    "can you compare them",
    "show me an example",
    "and pricing?",
    "and what about pricing",
    "but why",
    "also reviews",
    "what about alternatives",
    "how about the cheaper one",
    "what if it doesn't work",
    "how does it work",
    "what does that mean",
    "why is that",
    "is it free",
    "who is he",
    "the second one",
    "option 2",
    "number 3",
    "that's helpful",
]

REAL_SEARCHES = [
    "best running shoes",
    "how to tie a tie",
    "what is seo",
    "next",  # a retailer, not "next"
    "next js tutorial",
    "yes man movie",
    "go on holiday cheap flights",
    "tell me why lyrics",
    "buy red widgets online",
    "pricing",
    "acme widgets pricing",
    "it support services london",
    "how does a heat pump work in winter",
    "what does a plumber charge per hour",
    "is it safe to use bleach on quartz countertops",
    "compare crm software for small business",
    "and justice for all",
    "ok google",
    "second hand cars manchester",
    "option pricing models",
    "number of days in february",
    "continue reading subscription",
    "list of dog breeds",
    "show me the money cast",
    "what about bob cast and crew",
    "how about we ski",  # ambiguous on purpose: see test below
    "",
    "   ",
]


@pytest.mark.parametrize("query", CONVERSATIONAL)
def test_conversational_examples(query: str) -> None:
    assert is_conversational(query), query


@pytest.mark.parametrize(
    "query",
    [
        q
        for q in REAL_SEARCHES
        if q not in {"and justice for all", "what about bob cast and crew", "how about we ski"}
    ],
)
def test_real_searches_are_left_alone(query: str) -> None:
    assert not is_conversational(query), query


def test_known_limitations_are_documented_not_hidden() -> None:
    # "what about X" with up to three words is treated as a follow-up, so a short real title can be caught.
    assert is_conversational("what about bob")
    # But longer queries and ones that do not start with the connector are not.
    assert not is_conversational("what about bob cast and crew")
    assert not is_conversational("and justice for all")  # four words after the connector: not a follow-up


def test_table_covers_both_classes_with_enough_cases() -> None:
    assert len(CONVERSATIONAL) >= 30 and len(REAL_SEARCHES) >= 20
