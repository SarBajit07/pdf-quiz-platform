"""OpenAI-compatible JSON completion with provider fallback."""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMResponseError(ValueError):
    pass


class LLMClient(Protocol):
    async def complete_json[T](
        self, system: str, prompt: str, *, validate: Callable[[object], T],
    ) -> tuple[T, str]: ...


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


def configured_providers() -> list[Provider]:
    providers: list[Provider] = []
    for name in [settings.llm_provider, *settings.llm_fallbacks.split(",")]:
        name = name.strip().lower()
        if not name or any(item.name == name for item in providers):
            continue
        if name == settings.llm_provider:
            providers.append(Provider(name, settings.llm_base_url, settings.llm_api_key,
                                      settings.llm_model))
        elif name == "gemini" and settings.gemini_api_key:
            providers.append(Provider(name, "https://generativelanguage.googleapis.com/v1beta/openai",
                                      settings.gemini_api_key, settings.gemini_model))
        elif name == "groq" and settings.groq_api_key:
            providers.append(Provider(name, "https://api.groq.com/openai/v1",
                                      settings.groq_api_key, settings.groq_model))
        elif name == "openrouter" and settings.openrouter_api_key:
            providers.append(Provider(name, "https://openrouter.ai/api/v1",
                                      settings.openrouter_api_key, settings.openrouter_model))
    return providers


class OpenAICompatibleClient:
    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.providers = providers if providers is not None else configured_providers()
        self.semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self.rate_lock = asyncio.Lock()
        self.next_request: dict[str, float] = {}

    async def complete_json[T](
        self, system: str, prompt: str, *, validate: Callable[[object], T],
    ) -> tuple[T, str]:
        if not self.providers:
            raise LLMError("No LLM provider is configured")
        request_failed = False
        async with self.semaphore:
            for provider in self.providers:
                provider_prompt = prompt
                for attempt in range(2):
                    try:
                        async with self.rate_lock:
                            wait = self.next_request.get(provider.name, 0) - time.monotonic()
                            if wait > 0:
                                await asyncio.sleep(wait)
                            self.next_request[provider.name] = (
                                time.monotonic() + 60 / settings.llm_requests_per_minute
                            )
                        data, model = await self._request(provider, system, provider_prompt)
                        return validate(data), model
                    except (httpx.HTTPError, ValueError) as exc:
                        if isinstance(exc, httpx.HTTPError):
                            request_failed = True
                        else:
                            provider_prompt += (
                                "\nThe previous response was invalid. Follow the JSON contract."
                            )
                        logger.warning("LLM provider %s failed: %s", provider.name, exc)
                        if attempt == 0:
                            delay = 2
                            if (
                                isinstance(exc, httpx.HTTPStatusError)
                                and exc.response.status_code == 429
                            ):
                                try:
                                    delay = min(60, max(2, float(
                                        exc.response.headers.get("Retry-After", "2")
                                    )))
                                except ValueError:
                                    pass
                            await asyncio.sleep(delay)
            if request_failed:
                raise LLMError("All configured LLM providers failed or returned invalid responses")
            raise LLMResponseError("No provider returned a valid response")

    async def _request(
        self, provider: Provider, system: str, prompt: str,
    ) -> tuple[object, str]:
        url = provider.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": provider.model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                url, headers={"Authorization": f"Bearer {provider.api_key}"}, json=body,
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices:
                raise ValueError("LLM returned no choices")
            choice = choices[0]
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise ValueError("LLM returned no text")
            return json.loads(content), provider.model
