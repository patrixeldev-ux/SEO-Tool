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

    # --- Integrations (all optional: the app and every Phase 0-1 feature work when these are unset) ---
    # Where the browser is sent after the Google consent screen (the web app's address).
    web_base_url: str = "http://localhost:5173"
    # Google OAuth client ("Web application") for Search Console. See docs/GOOGLE_SETUP.md.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:8000/integrations/google/callback"
    # Fernet key that encrypts stored OAuth tokens and API keys. In development it is derived from
    # JWT_SECRET when unset (with a warning); in production the app refuses to store credentials without it.
    token_encryption_key: SecretStr | None = None
    # Optional PageSpeed Insights API key (raises the quota; PSI also works without a key, slowly).
    psi_api_key: SecretStr | None = None
    # Set to false to switch off the PageSpeed step of audits.
    psi_enabled: bool = True

    # --- AI suggestions (Phase 3). All optional: without a key the planner still lists every action and
    # marks the ones that need a written value as "needs AI". Keys are set here, never in the web app.
    llm_provider: Literal["anthropic", "gemini", "fake"] | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    # Tokens an organisation may spend on AI calls per calendar month (changeable per organisation).
    llm_default_monthly_token_budget: int = Field(default=2_000_000, ge=0)

    # --- Deployers (Phase 4). All optional; the defaults are the real vendor endpoints. ---
    # The API base URLs can be pointed at a local stand-in for tests and the dev smoke run.
    # The address the Cloudflare Worker uses to fetch its (signed) rules: it must be reachable from the internet.
    public_api_url: str = "http://localhost:8000"
    github_api_base: str = "https://api.github.com"
    # Development and tests only: let the deploy tooling fetch pages on private / loopback addresses
    # (the ownership file check, the live-HTML read before a deploy, the post-deploy verification).
    deploy_allow_private_hosts: bool = False

    @field_validator(
        "google_client_id",
        "google_client_secret",
        "token_encryption_key",
        "psi_api_key",
        "llm_provider",
        "anthropic_api_key",
        "gemini_api_key",
        "gemini_model",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """`GOOGLE_CLIENT_ID=` (empty, as in .env.example) means "not configured"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

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
            if self.llm_provider == "fake":
                raise ValueError("LLM_PROVIDER=fake is for development and tests only")
            if self.deploy_allow_private_hosts:
                raise ValueError("DEPLOY_ALLOW_PRIVATE_HOSTS is for development and tests only")
        return self

    @property
    def refresh_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
