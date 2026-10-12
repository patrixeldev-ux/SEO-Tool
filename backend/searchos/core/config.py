"""Application settings. Every secret comes from the environment; nothing secret has a default."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Values shipped in .env.example. They are fine for local development and must never reach production.
INSECURE_DEV_SECRETS = frozenset({"dev-only-insecure-secret-change-me-0123456789"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    # Required, no defaults: the app refuses to start without them.
    database_url: str
    jwt_secret: SecretStr

    redis_url: str = "redis://localhost:6379/0"

    # Auth
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=240)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    refresh_cookie_name: str = "searchos_refresh"
    refresh_cookie_path: str = "/auth"
    # None = derive from environment (Secure cookies in production only).
    cookie_secure: bool | None = None

    # CORS: comma-separated list of exact origins. Never "*" (we send credentials).
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Crawler politeness: requests per second to any single host (can be lowered per deployment).
    crawl_rate_per_host: float = Field(default=2.0, gt=0, le=50)

    git_sha: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip().rstrip("/") for part in value.split(",") if part.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str]) -> list[str]:
        if any(origin == "*" for origin in value):
            raise ValueError("CORS_ORIGINS must list exact origins; '*' is not allowed")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if self.jwt_secret.get_secret_value() in INSECURE_DEV_SECRETS:
                raise ValueError("JWT_SECRET is the insecure development placeholder")
            if self.cookie_secure is False:
                raise ValueError("COOKIE_SECURE=false is not allowed in production")
        return self

    @property
    def refresh_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
