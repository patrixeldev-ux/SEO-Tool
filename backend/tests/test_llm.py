"""3.3: the AI layer - providers (HTTP mocked), selection from the environment, the token budget, the fake."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
import respx
from google import genai
from google.genai import types as genai_types
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import Settings
from searchos.llm import (
    LLMBudgetExceeded,
    LLMError,
    LLMProvider,
    Usage,
    get_provider,
    provider_status,
)
from searchos.llm.anthropic_provider import AnthropicProvider
from searchos.llm.budget import (
    budget_for,
    ensure_within_budget,
    estimate_tokens,
    month_start,
    record_usage,
    used_this_month,
)
from searchos.llm.fake import FakeProvider, default_handler, parse_prompt
from searchos.llm.gemini_provider import GeminiProvider
from searchos.models import LlmUsage, Org
from searchos.planner.generate import SCHEMAS, build_prompt

DB = "postgresql+asyncpg://u:p@localhost/db"
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"database_url": DB, "jwt_secret": "x" * 40}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


# ---- Anthropic ------------------------------------------------------------------------------------------
def anthropic_body(**input_: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": "answer", "input": input_}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


class Recorder:
    """The anthropic SDK 1.x ships its own copy of httpx (`httpx2`), which respx cannot patch: mock its transport."""

    def __init__(self, response: httpx2.Response) -> None:
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.response


def anthropic_provider(
    response: httpx2.Response, key: str = "sk-ant-test"
) -> tuple[AnthropicProvider, Recorder]:
    recorder = Recorder(response)
    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(recorder))
    client = anthropic.AsyncAnthropic(api_key=key, http_client=http, max_retries=0)
    return AnthropicProvider(key, "claude-sonnet-5-5", client=client), recorder


async def test_anthropic_returns_the_forced_tool_input_and_usage() -> None:
    provider, rec = anthropic_provider(
        httpx2.Response(200, json=anthropic_body(title="Fresh roasted coffee beans"))
    )
    assert isinstance(provider, LLMProvider)
    result = await provider.generate_json("system text", "user text", SCHEMA)
    assert result == {"title": "Fresh roasted coffee beans"}
    assert provider.last_usage == Usage(120, 30) and provider.last_usage.total == 150
    request = rec.requests[-1]
    assert request.url.path == "/v1/messages"
    sent = json.loads(request.content)
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["system"] == "system text"
    assert sent["messages"] == [{"role": "user", "content": "user text"}]
    assert sent["tool_choice"] == {"type": "tool", "name": "answer"}
    assert sent["tools"][0]["input_schema"] == SCHEMA
    assert request.headers["x-api-key"] == "sk-ant-test"


async def test_anthropic_without_the_structure_is_an_error() -> None:
    body = anthropic_body()
    body["content"] = [{"type": "text", "text": "Sure! Here you go."}]
    body["stop_reason"] = "end_turn"
    provider, _ = anthropic_provider(httpx2.Response(200, json=body))
    with pytest.raises(LLMError, match="without the requested structure"):
        await provider.generate_json("s", "u", SCHEMA)


async def test_anthropic_api_errors_become_llm_errors_without_the_key() -> None:
    provider, _ = anthropic_provider(
        httpx2.Response(
            401,
            json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}},
        ),
        key="sk-ant-secret-value",
    )
    with pytest.raises(LLMError, match="AuthenticationError") as caught:
        await provider.generate_json("s", "u", SCHEMA)
    assert "sk-ant-secret-value" not in str(caught.value)


# ---- Gemini ---------------------------------------------------------------------------------------------
def gemini_body(text: str) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 90,
            "candidatesTokenCount": 20,
            "thoughtsTokenCount": 5,
            "totalTokenCount": 115,
        },
    }


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"


def gemini(**kw: Any) -> GeminiProvider:
    client = genai.Client(
        api_key="g-test",
        http_options=genai_types.HttpOptions(retry_options=genai_types.HttpRetryOptions(attempts=1)),
    )
    return GeminiProvider("g-test", "gemini-test", client=client, **kw)


@respx.mock
async def test_gemini_parses_json_text_and_counts_thinking_tokens() -> None:
    route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=gemini_body('{"title": "Pour over coffee guide"}'))
    )
    provider = gemini()
    assert isinstance(provider, LLMProvider)
    assert await provider.generate_json("sys", "usr", SCHEMA) == {"title": "Pour over coffee guide"}
    assert provider.last_usage == Usage(90, 25)
    sent = json.loads(route.calls.last.request.content)
    assert sent["generationConfig"]["responseMimeType"] == "application/json"
    assert sent["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert sent["systemInstruction"]["parts"][0]["text"] == "sys"
    assert route.calls.last.request.headers["x-goog-api-key"] == "g-test"


@respx.mock
@pytest.mark.parametrize("text", ["not json at all", "[1, 2]", ""])
async def test_gemini_unusable_answers_are_errors(text: str) -> None:
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=gemini_body(text)))
    with pytest.raises(LLMError):
        await gemini().generate_json("s", "u", SCHEMA)


@respx.mock
async def test_gemini_api_errors_become_llm_errors() -> None:
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            429, json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
        )
    )
    with pytest.raises(LLMError, match="Gemini request failed"):
        await gemini().generate_json("s", "u", SCHEMA)


# ---- the fake -------------------------------------------------------------------------------------------
async def test_fake_scripted_responses_are_consumed_in_order_and_exceptions_raised() -> None:
    fake = FakeProvider(responses=[{"title": "a"}, LLMError("boom")], tokens=(5, 2))
    assert await fake.generate_json("s", "u", SCHEMA) == {"title": "a"}
    assert fake.last_usage == Usage(5, 2)
    with pytest.raises(LLMError, match="boom"):
        await fake.generate_json("s", "u", SCHEMA)
    with pytest.raises(LLMError, match="no scripted response"):
        await fake.generate_json("s", "u", SCHEMA)
    assert len(fake.calls) == 3


async def test_fake_default_handler_reads_the_prompt_data_block() -> None:
    data = {
        "page_title": "Grinders",
        "h1": "Choosing a coffee grinder",
        "brand": "Beanly",
        "slug": "grinders",
        "target_query": "burr grinder",
        "excerpt": "A burr grinder produces even particles. More text follows.",
        "domain": "beanly.example",
    }
    prompt = build_prompt("rewrite_title", data, [])
    assert parse_prompt(prompt) == ("rewrite_title", data)
    out = await FakeProvider().generate_json("s", prompt, SCHEMAS["rewrite_title"])
    assert 30 <= len(out["title"]) <= 60
    meta = default_handler("rewrite_meta", data)["description"]
    assert 120 <= len(meta) <= 155
    with pytest.raises(LLMError):
        default_handler("unknown_task", data)
    with pytest.raises(LLMError, match="no TASK line"):
        parse_prompt("hello")


# ---- selection and status -------------------------------------------------------------------------------
def test_nothing_configured_means_no_provider_and_no_problem() -> None:
    s = settings()
    status = provider_status(s)
    assert not status.configured and status.provider is None and status.problem is None
    assert not status.anthropic_key_present and not status.gemini_key_present
    assert get_provider(s) is None


def test_default_is_whichever_key_is_present_anthropic_first() -> None:
    both = settings(anthropic_api_key="a-key", gemini_api_key="g-key", gemini_model="gemini-test")
    status = provider_status(both)
    assert (status.provider, status.model, status.selected_by) == (
        "anthropic",
        "claude-sonnet-5-5",
        "key present",
    )
    only_gemini = settings(gemini_api_key="g-key", gemini_model="gemini-test")
    assert provider_status(only_gemini).provider == "gemini"
    provider = get_provider(only_gemini)
    assert provider is not None and provider.name == "gemini" and provider.model == "gemini-test"
    chosen = get_provider(both)
    assert chosen is not None and chosen.name == "anthropic"


def test_llm_provider_overrides_the_default() -> None:
    s = settings(llm_provider="gemini", anthropic_api_key="a", gemini_api_key="g", gemini_model="gm")
    assert provider_status(s).provider == "gemini" and provider_status(s).selected_by == "LLM_PROVIDER"
    s2 = settings(llm_provider="anthropic", anthropic_api_key="a", anthropic_model="claude-x")
    assert provider_status(s2).model == "claude-x"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"llm_provider": "anthropic"}, "ANTHROPIC_API_KEY"),
        ({"llm_provider": "gemini"}, "GEMINI_API_KEY"),
        ({"llm_provider": "gemini", "gemini_api_key": "g"}, "GEMINI_MODEL"),
        ({"gemini_api_key": "g"}, "GEMINI_MODEL"),
    ],
)
def test_misconfiguration_is_reported_not_raised(overrides: dict[str, object], fragment: str) -> None:
    s = settings(**overrides)
    status = provider_status(s)
    assert not status.configured and status.problem and fragment in status.problem
    assert get_provider(s) is None


def test_blank_values_count_as_unset() -> None:
    s = settings(anthropic_api_key="  ", gemini_api_key="", gemini_model="", llm_provider="")
    assert s.anthropic_api_key is None and s.gemini_api_key is None and s.llm_provider is None
    assert get_provider(s) is None


def test_status_never_contains_a_key() -> None:
    s = settings(anthropic_api_key="sk-ant-very-secret")
    assert "very-secret" not in repr(provider_status(s))
    assert "very-secret" not in repr(s)


def test_fake_provider_is_available_in_development_and_refused_in_production() -> None:
    dev = settings(llm_provider="fake")
    provider = get_provider(dev)
    assert provider is not None and provider.name == "fake"
    with pytest.raises(ValidationError, match="development and tests only"):
        settings(llm_provider="fake", environment="production", cookie_secure=True, jwt_secret="y" * 40)


# ---- the monthly budget ---------------------------------------------------------------------------------
async def make_org(session: AsyncSession, budget: int | None = None) -> uuid.UUID:
    org = Org(name=f"Org {uuid.uuid4().hex[:6]}", llm_token_budget=budget)
    session.add(org)
    await session.commit()
    return org.id


async def test_default_budget_comes_from_settings_and_an_org_can_override_it(session: AsyncSession) -> None:
    plain = await make_org(session)
    custom = await make_org(session, 50_000)
    assert await budget_for(session, plain) == 2_000_000
    assert await budget_for(session, custom) == 50_000


async def test_usage_is_summed_per_org_and_month(session: AsyncSession) -> None:
    org, other = await make_org(session), await make_org(session)
    for amount in (Usage(100, 20), Usage(300, 80)):
        await record_usage(
            session, org_id=org, site_id=None, provider="fake", model="m", purpose="t", usage=amount, ok=True
        )
    await record_usage(
        session,
        org_id=other,
        site_id=None,
        provider="fake",
        model="m",
        purpose="t",
        usage=Usage(9, 9),
        ok=False,
    )
    assert await used_this_month(session, org) == 500
    assert await used_this_month(session, other) == 18
    assert month_start(datetime(2026, 10, 12, 15, 30, tzinfo=UTC)) == datetime(2026, 10, 1, tzinfo=UTC)


async def test_last_months_usage_does_not_count(session: AsyncSession) -> None:
    org = await make_org(session)
    session.add(
        LlmUsage(
            org_id=org,
            provider="fake",
            model="m",
            purpose="t",
            input_tokens=900,
            output_tokens=100,
            ok=True,
            created_at=month_start() - timedelta(days=3),
        )
    )
    await session.commit()
    assert await used_this_month(session, org) == 0


async def test_budget_is_enforced_before_the_call(session: AsyncSession) -> None:
    org = await make_org(session, 1_000)
    await ensure_within_budget(session, org, 400)
    await record_usage(
        session,
        org_id=org,
        site_id=None,
        provider="fake",
        model="m",
        purpose="t",
        usage=Usage(500, 300),
        ok=True,
    )
    await ensure_within_budget(session, org, 200)  # 800 + 200 == 1000: still allowed
    with pytest.raises(LLMBudgetExceeded, match="1,000"):
        await ensure_within_budget(session, org, 201)


async def test_a_zero_budget_blocks_everything(session: AsyncSession) -> None:
    org = await make_org(session, 0)
    with pytest.raises(LLMBudgetExceeded):
        await ensure_within_budget(session, org, 1)


def test_estimate_is_a_cheap_upper_bound() -> None:
    assert estimate_tokens("a" * 300, "b" * 300) == 200 + 600
    assert estimate_tokens("", output=0) == 0
