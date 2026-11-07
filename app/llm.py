"""LLM clients. Vultr Serverless Inference (OpenAI-compatible) is the default provider."""
from __future__ import annotations

import asyncio
from typing import Protocol

import httpx

from app.config import Settings

Message = dict[str, str]


class LLMError(RuntimeError):
    """Raised when the model provider fails."""


class LLMClient(Protocol):
    async def complete(self, messages: list[Message]) -> str: ...


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._model = model

    async def complete(self, messages: list[Message]) -> str:
        payload = {"model": self._model, "messages": messages, "temperature": 0.1, "max_tokens": 2500}
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    self._url, json=payload, headers={"Authorization": f"Bearer {self._key}"}
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"Vultr inference unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise LLMError(f"Vultr inference HTTP {r.status_code}: {r.text[:300]}")
        try:
            return str(r.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Malformed inference response") from exc


class GeminiClient:
    """Google Gemini (free tier works). Note: Challenge 1 judging expects Vultr inference."""

    def __init__(self, api_key: str, model: str) -> None:
        self._url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        self._key = api_key

    async def _post_with_retry(self, body: dict[str, object]) -> httpx.Response:
        """Free tier is rate limited: retry 429/503 with exponential backoff (max ~30 s)."""
        r: httpx.Response | None = None
        for delay in (0, 4, 10, 20):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    r = await client.post(self._url, json=body, headers={"x-goog-api-key": self._key})
            except httpx.HTTPError as exc:
                raise LLMError(f"Gemini unreachable ({type(exc).__name__}). Check internet and API key.") from exc
            if r.status_code not in (429, 503):
                break
        assert r is not None
        return r

    async def complete(self, messages: list[Message]) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
            if m["role"] != "system"
        ]
        body: dict[str, object] = {"contents": contents, "generationConfig": {"temperature": 0.1}}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        r = await self._post_with_retry(body)
        if r.status_code != 200:
            raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(str(p["text"]) for p in parts if "text" in p and not p.get("thought"))
            if not text:
                raise KeyError("no text part")
            return text
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError("Malformed Gemini response") from exc


def resolve_provider(s: Settings) -> str:
    if s.provider == "auto":
        return "vultr" if s.vultr_api_key and s.vultr_model else "gemini"
    return s.provider


def build_client(s: Settings) -> LLMClient:
    if resolve_provider(s) == "gemini":
        return GeminiClient(s.gemini_api_key, s.gemini_model)
    return OpenAICompatClient(s.vultr_base_url, s.vultr_api_key, s.vultr_model)
