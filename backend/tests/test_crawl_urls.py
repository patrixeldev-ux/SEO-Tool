from __future__ import annotations

import pytest

from searchos.crawl.urls import InvalidUrlError, normalise_netloc, normalise_url, path_of, resolve_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP://Example.COM", "http://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://user:pw@example.com/a", "https://example.com/a"),
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),
        ("https://example.com/a/b/..", "https://example.com/a/"),
        ("https://example.com/%7Euser/%e2%82%ac", "https://example.com/~user/%E2%82%AC"),
        ("https://example.com/a b", "https://example.com/a%20b"),
        ("https://example.com/p?b=1&a=2", "https://example.com/p?b=1&a=2"),
        ("https://bücher.example/x", "https://xn--bcher-kva.example/x"),
        ("https://example.com/dir/", "https://example.com/dir/"),
        ("https://example.com/dir", "https://example.com/dir"),
    ],
)
def test_normalise_url(raw: str, expected: str) -> None:
    assert normalise_url(raw) == expected


@pytest.mark.parametrize(
    "raw", ["ftp://example.com/", "mailto:a@b.c", "/relative", "http://", "http://h:99999/"]
)
def test_normalise_rejects_non_http(raw: str) -> None:
    with pytest.raises(InvalidUrlError):
        normalise_url(raw)


def test_trailing_slash_is_a_different_page() -> None:
    assert normalise_url("https://e.com/a") != normalise_url("https://e.com/a/")


def test_resolve_url_handles_relative_and_non_web_links() -> None:
    base = "https://e.com/blog/post.html"
    assert resolve_url(base, "../about") == "https://e.com/about"
    assert resolve_url(base, "//cdn.e.com/x.js") == "https://cdn.e.com/x.js"
    assert resolve_url(base, "other.html#top") == "https://e.com/blog/other.html"
    assert resolve_url(base, "?page=2") == "https://e.com/blog/post.html?page=2"
    for href in ["", "#x", "mailto:a@b.c", "tel:123", "javascript:void(0)", "data:text/plain,hi"]:
        assert resolve_url(base, href) is None


def test_normalise_netloc_and_path_of() -> None:
    assert normalise_netloc("Example.com") == "example.com"
    assert normalise_netloc("127.0.0.1:8000") == "127.0.0.1:8000"
    assert path_of("https://e.com/a/b?x=1") == "/a/b?x=1"
    assert path_of("https://e.com") == "/"
