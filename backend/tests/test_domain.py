import pytest

from searchos.core.domain import InvalidDomainError, normalize_domain


@pytest.mark.parametrize(
    ("raw", "domain", "uses_www"),
    [
        ("example.com", "example.com", False),
        ("  Example.COM  ", "example.com", False),
        ("https://example.com", "example.com", False),
        ("http://example.com/some/path?q=1#frag", "example.com", False),
        ("HTTPS://WWW.Example.com/", "example.com", True),
        ("www.example.com", "example.com", True),
        ("example.com.", "example.com", False),
        ("blog.example.co.uk", "blog.example.co.uk", False),
        ("www.blog.example.co.uk", "blog.example.co.uk", True),
        ("bücher.de", "xn--bcher-kva.de", False),
        ("https://www.münchen.de/x", "xn--mnchen-3ya.de", True),
        ("xn--bcher-kva.de", "xn--bcher-kva.de", False),
        ("shop.example", "shop.example", False),
        ("www.com", "www.com", False),  # the registrable domain itself is "www.com"
    ],
)
def test_normalizes(raw: str, domain: str, uses_www: bool) -> None:
    result = normalize_domain(raw)
    assert result.domain == domain
    assert result.uses_www is uses_www


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "localhost",
        "no spaces.com",
        "example",
        "192.168.1.10",
        "http://user:pass@example.com",
        "example.com:8080",
        "-bad-.com",
        "exa_mple.com",
        "a" * 64 + ".com",
        "http://",
        "example..com",
    ],
)
def test_rejects(raw: str) -> None:
    with pytest.raises(InvalidDomainError):
        normalize_domain(raw)


def test_idempotent() -> None:
    first = normalize_domain("https://www.Bücher.DE/path")
    again = normalize_domain(first.domain)
    assert again.domain == first.domain
