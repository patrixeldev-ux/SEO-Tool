"""Heuristic: does a Search Console query look like a follow-up typed into an AI conversation?

AI Mode / chat-style search sends turns such as "yes", "go on", "tell me more", "and pricing?" or "how does it
work" as queries. They have no meaning outside their conversation, so they are shown as a separate segment and
kept out of the CTR curve and the opportunity finders.

This is a **heuristic** (no language model, no part-of-speech tagger) and it will be wrong sometimes: a real
search for the band "The Who" is all function words. It is tuned to prefer leaving a query alone, and every place
that shows the segment labels it "heuristic".

A query is conversational when, after lower-casing and stripping punctuation, one of these holds:

1. FILLER      every word is a function word, filler or discourse verb ("yes", "tell me more", "go on", "thanks"),
               up to 4 words, or up to 8 when it starts with an affirmation ("yes please go on with the second one");
2. CONTINUER   it starts with a connector and adds at most two words: "and pricing", "but why", "also reviews";
3. ABOUT       "what about / how about / what if" followed by at most three words;
4. ANAPHORA    it starts with a question or request word, refers back with a pronoun ("it", "that", "them") and has
               at most one content word: "how does it work", "what does that mean", "can you compare them";
5. CHOICE      picks from a previous list: "the second one", "option 2", "number 3".
"""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^a-z0-9\s]")

AFFIRMATIONS = frozenset(
    [
        "yes",
        "yeah",
        "yep",
        "yup",
        "yea",
        "ok",
        "okay",
        "k",
        "sure",
        "no",
        "nope",
        "nah",
        "please",
        "pls",
        "thanks",
        "thank",
        "thx",
        "ty",
        "right",
    ]
)
PRONOUNS = frozenset(
    [
        "it",
        "its",
        "that",
        "this",
        "those",
        "these",
        "them",
        "they",
        "he",
        "she",
        "his",
        "her",
        "their",
        "thats",
    ]
)
QUESTION_STARTS = frozenset(
    [
        "what",
        "why",
        "how",
        "when",
        "where",
        "who",
        "which",
        "is",
        "are",
        "does",
        "do",
        "did",
        "can",
        "could",
        "would",
        "should",
        "will",
        "tell",
        "show",
        "explain",
        "give",
        "compare",
        "summarize",
        "summarise",
        "list",
        "elaborate",
    ]
)
# Words that carry no topic of their own. "next" is deliberately absent (it is a retailer), as are "more" nouns.
LIGHT_WORDS = frozenset(
    [
        "continue",
        "more",
        "go",
        "on",
        "ahead",
        "keep",
        "going",
        "carry",
        "proceed",
        "really",
        "and",
        "but",
        "also",
        "then",
        "so",
        "got",
        "i",
        "see",
        "interesting",
        "great",
        "cool",
        "nice",
        "good",
        "perfect",
        "awesome",
        "helpful",
        "amazing",
        "the",
        "a",
        "an",
        "of",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "will",
        "me",
        "my",
        "we",
        "us",
        "you",
        "your",
        "to",
        "for",
        "about",
        "with",
        "in",
        "at",
        "from",
        "by",
        "as",
        "if",
        "or",
        "not",
        "just",
        "now",
        "too",
        "very",
        "much",
        "many",
        "some",
        "any",
        "tell",
        "show",
        "explain",
        "give",
        "elaborate",
        "detail",
        "details",
        "example",
        "examples",
        "again",
        "compare",
        "summarize",
        "summarise",
        "list",
        "rewrite",
        "shorten",
        "simplify",
        "translate",
        "repeat",
        "first",
        "second",
        "third",
        "last",
        "other",
        "previous",
        "one",
        "ones",
        "want",
        "need",
        "like",
        "please",
        "what",
        "when",
        "where",
        "who",
        "which",
        "why",
        "how",
        "mean",
        "means",
        "work",
        "works",
        "it",
        "its",
        "that",
        "this",
        "those",
        "these",
        "them",
        "they",
        "he",
        "she",
        "his",
        "her",
        "their",
        "thats",
        "yes",
        "yeah",
        "yep",
        "yup",
        "yea",
        "ok",
        "okay",
        "k",
        "sure",
        "no",
        "nope",
        "nah",
        "pls",
        "thanks",
        "thank",
        "thx",
        "ty",
        "right",
    ]
)
CONNECTORS = frozenset(["and", "but", "also", "then", "so"])

_CHOICE = re.compile(
    r"^(?:the )?(?:first|second|third|fourth|last|other|previous|1st|2nd|3rd|4th)"
    r"(?: one| option| result| link| choice)?$|^(?:option|number|no|choice|result) ?\d{1,2}$"
)
_ABOUT = ("what about", "how about", "what if", "and what about", "and how about")

MAX_FILLER_WORDS = 4
MAX_FILLER_WORDS_AFTER_AFFIRMATION = 8


def _tokens(query: str) -> list[str]:
    text = query.lower().replace("'", "").replace("\u2019", "")
    return _NON_WORD.sub(" ", text).split()


def is_conversational(query: str) -> bool:
    words = _tokens(query)
    if not words:
        return False
    text = " ".join(words)

    # 5. CHOICE
    if _CHOICE.match(text):
        return True

    # 1. FILLER
    limit = MAX_FILLER_WORDS_AFTER_AFFIRMATION if words[0] in AFFIRMATIONS else MAX_FILLER_WORDS
    if len(words) <= limit and all(w in LIGHT_WORDS for w in words):
        return True

    # 3. ABOUT
    for lead in _ABOUT:
        if text.startswith(lead + " "):
            rest = text[len(lead) :].split()
            if 1 <= len(rest) <= 3:
                return True

    # 2. CONTINUER
    if words[0] in CONNECTORS and 2 <= len(words) <= 3:
        return True

    # 4. ANAPHORA
    if words[0] in QUESTION_STARTS and len(words) <= 7 and any(w in PRONOUNS for w in words[1:]):
        content = [w for w in words if w not in LIGHT_WORDS and w not in QUESTION_STARTS]
        if len(content) <= 1:
            return True
    return False
