"""The provider interface every AI adapter implements, and the errors the planner understands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class LLMError(Exception):
    """A call to the AI provider failed (network, quota, refusal, unusable answer)."""


class LLMBudgetExceeded(LLMError):
    """The organisation has used its monthly AI token budget."""


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@runtime_checkable
class LLMProvider(Protocol):
    """One adapter per vendor. Always asks for structured JSON; the caller validates it.

    `last_usage` is the token count of the most recent `generate_json` call on this instance. The planner makes
    its calls one after another per instance (it builds an instance per run), so no locking is needed.
    """

    name: str  # "anthropic" | "gemini" | "fake"
    model: str
    last_usage: Usage

    async def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]: ...
