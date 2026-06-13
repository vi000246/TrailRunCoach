"""AI provider clients with lazy SDK imports to avoid hard dependency errors."""
from __future__ import annotations
from typing import AsyncIterator

CLAUDE_MODELS = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

OPENAI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "o1-mini",
]

GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# Google exposes an OpenAI-compatible endpoint, so Gemini reuses the OpenAI SDK
# with a custom base_url — no extra dependency needed.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class ClaudeClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed")

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        async with client.messages.stream(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as s:
            async for text in s.text_stream:
                yield text


class OpenAIClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai package not installed")

        client = AsyncOpenAI(api_key=self.api_key)
        response = await client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            stream=True,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


class GeminiClient:
    """Gemini via Google's OpenAI-compatible endpoint (reuses the OpenAI SDK)."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = GEMINI_BASE_URL

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai package not installed")

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        response = await client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            stream=True,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def get_ai_client(provider: str, api_key: str, model: str) -> ClaudeClient | OpenAIClient | GeminiClient:
    if provider == "claude":
        return ClaudeClient(api_key=api_key, model=model)
    if provider == "openai":
        return OpenAIClient(api_key=api_key, model=model)
    if provider == "gemini":
        return GeminiClient(api_key=api_key, model=model)
    raise ValueError(f"Unknown provider: {provider}")
