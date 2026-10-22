"""Optional model-provider adapter. Default is disabled; the app works fully without it."""
from __future__ import annotations

import json
from typing import Protocol

import httpx

from app.config import Settings
from app.prompts import build_messages
from app.schemas import AppError


class ModelProvider(Protocol):
    async def extract_request(self, text: str) -> dict: ...


class NullProvider:
    async def extract_request(self, text: str) -> dict:
        raise AppError("LLM_DISABLED", "Natural-language intake is not enabled.", status_code=503,
                       user_action="Use the form to enter blood group, component and quantity.")


class OpenAIProvider:
    """Not exercised against the live API in this repo's tests; verify before enabling."""

    def __init__(self, api_key: str, model: str, timeout_s: float = 10.0):
        self._key, self._model, self._timeout = api_key, model, timeout_s

    async def extract_request(self, text: str) -> dict:
        body = {"model": self._model, "temperature": 0,
                "response_format": {"type": "json_object"}, "messages": build_messages(text)}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post("https://api.openai.com/v1/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {self._key}"})
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as e:
            raise AppError("LLM_UNAVAILABLE", "Intake helper is unavailable.", status_code=503,
                           retryable=True, user_action="Use the form instead.") from e


def build_provider(s: Settings) -> ModelProvider:
    key = s.llm_api_key.get_secret_value()
    if s.llm_provider == "openai" and key and s.llm_model:
        return OpenAIProvider(key, s.llm_model)
    return NullProvider()
