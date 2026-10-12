"""Gemini via the official `google-genai` SDK, asking for JSON that matches a schema."""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import errors, types

from searchos.llm.base import LLMError, Usage

MAX_OUTPUT_TOKENS = 1500


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, *, client: genai.Client | None = None) -> None:
        self.model = model
        self.last_usage = Usage()
        self._client = client or genai.Client(api_key=api_key)

    async def generate_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.4,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=user, config=config
            )
        except errors.APIError as exc:
            raise LLMError(f"Gemini request failed: {type(exc).__name__}: {exc}"[:300]) from None
        meta = response.usage_metadata
        if meta is not None:
            self.last_usage = Usage(
                int(meta.prompt_token_count or 0),
                int((meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)),
            )
        text = response.text
        if not text:
            raise LLMError("Gemini returned no text")
        try:
            data = json.loads(text)
        except ValueError:
            raise LLMError("Gemini did not return valid JSON") from None
        if not isinstance(data, dict):
            raise LLMError("Gemini did not return a JSON object")
        return data
