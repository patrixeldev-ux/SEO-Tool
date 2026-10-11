import pytest
from pydantic import ValidationError

from searchos.core.config import Settings

DB = "postgresql+asyncpg://u:p@localhost/db"
GOOD_SECRET = "x" * 40


def make(**overrides: object) -> Settings:
    base: dict[str, object] = {"database_url": DB, "jwt_secret": GOOD_SECRET}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_secrets_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_short_jwt_secret_rejected() -> None:
    with pytest.raises(ValidationError):
        make(jwt_secret="short")


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, https://b.example/")
    settings = Settings(_env_file=None, database_url=DB, jwt_secret=GOOD_SECRET)  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://a.example", "https://b.example"]


def test_wildcard_cors_rejected() -> None:
    with pytest.raises(ValidationError):
        make(cors_origins=["*"])


def test_cookie_secure_follows_environment() -> None:
    assert make(environment="development").refresh_cookie_secure is False
    assert make(environment="production").refresh_cookie_secure is True


def test_production_refuses_insecure_placeholder_secret() -> None:
    with pytest.raises(ValidationError):
        make(environment="production", jwt_secret="dev-only-insecure-secret-change-me-0123456789")


def test_production_refuses_insecure_cookie() -> None:
    with pytest.raises(ValidationError):
        make(environment="production", cookie_secure=False)
