"""Claude via the official `anthropic` SDK. Structured output = one forced tool call whose input is the answer."""

from __future__ import annotations

from typing import Any

import anthropic

from searchos.llm.base import LLMError, Usage

TOOL_NAME = "answer"
MAX_OUTPUT_TOKENS = 1500


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, client: anthropic.AsyncAnthropic | None = None) -> None:
        self.model = model
        self.last_usage = Usage()
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60.0)

    async def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "name": TOOL_NAME,
                        "description": "Return the answer in exactly this structure.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic request failed: {type(exc).__name__}: {exc}"[:300]) from None
        usage = getattr(response, "usage", None)
        self.last_usage = Usage(
            int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
        )
        for block in response.content:
            if isinstance(block, anthropic.types.ToolUseBlock) and isinstance(block.input, dict):
                return dict(block.input)
        raise LLMError("Anthropic answered without the requested structure")
