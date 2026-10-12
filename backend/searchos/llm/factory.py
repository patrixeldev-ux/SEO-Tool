"""Chooses the AI provider from the environment. Keys live in `.env`; the app only ever reports whether they exist."""

from __future__ import annotations

from dataclasses import dataclass

from searchos.core.config import Settings, get_settings
from searchos.llm.base import LLMProvider

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    configured: bool
    provider: str | None  # the provider that will be used
    model: str | None
    selected_by: str | None  # "LLM_PROVIDER" | "key present" | None
    anthropic_key_present: bool
    gemini_key_present: bool
    gemini_model_set: bool
    problem: str | None  # why nothing is usable although something was asked for


def _status(settings: Settings) -> ProviderStatus:
    anthropic_key = settings.anthropic_api_key is not None
    gemini_key = settings.gemini_api_key is not None
    gemini_model = bool(settings.gemini_model)
    base = {
        "anthropic_key_present": anthropic_key,
        "gemini_key_present": gemini_key,
        "gemini_model_set": gemini_model,
    }

    def ok(provider: str, model: str, by: str) -> ProviderStatus:
        return ProviderStatus(True, provider, model, by, problem=None, **base)

    def bad(problem: str) -> ProviderStatus:
        return ProviderStatus(False, None, None, None, problem=problem, **base)

    anthropic_model = settings.anthropic_model or DEFAULT_ANTHROPIC_MODEL
    wanted = settings.llm_provider
    if wanted == "fake":
        return ok("fake", "fake-1", "LLM_PROVIDER")
    if wanted == "anthropic":
        return (
            ok("anthropic", anthropic_model, "LLM_PROVIDER")
            if anthropic_key
            else bad("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        )
    if wanted == "gemini":
        if not gemini_key:
            return bad("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.")
        if not gemini_model:
            return bad("GEMINI_MODEL is required when using Gemini; set it in .env.")
        return ok("gemini", str(settings.gemini_model), "LLM_PROVIDER")
    if anthropic_key:
        return ok("anthropic", anthropic_model, "key present")
    if gemini_key:
        if not gemini_model:
            return bad("GEMINI_API_KEY is set but GEMINI_MODEL is not; set the model name in .env.")
        return ok("gemini", str(settings.gemini_model), "key present")
    return ProviderStatus(False, None, None, None, problem=None, **base)


def provider_status(settings: Settings | None = None) -> ProviderStatus:
    return _status(settings or get_settings())


def get_provider(settings: Settings | None = None) -> LLMProvider | None:
    """The configured provider, or None when no usable key is set (the planner then marks value actions needs_ai)."""
    settings = settings or get_settings()
    status = _status(settings)
    if not status.configured or status.provider is None or status.model is None:
        return None
    if status.provider == "fake":
        from searchos.llm.fake import FakeProvider

        return FakeProvider()
    if status.provider == "anthropic":
        from searchos.llm.anthropic_provider import AnthropicProvider

        if settings.anthropic_api_key is None:
            return None
        return AnthropicProvider(settings.anthropic_api_key.get_secret_value(), status.model)
    from searchos.llm.gemini_provider import GeminiProvider

    if settings.gemini_api_key is None:
        return None
    return GeminiProvider(settings.gemini_api_key.get_secret_value(), status.model)
