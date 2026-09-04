from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Type

from openai import AsyncOpenAI
from pydantic import BaseModel


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from model output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Fallback: find the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise ValueError(f"no JSON object found in model output: {text[:200]}")


class LLMGateway:
    """OpenAI-compatible chat completions gateway with JSON fallback."""

    def __init__(self, base_url: str, api_key: str, max_concurrency: int = 4):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._sem = asyncio.Semaphore(max_concurrency)
        self.usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }

    def _add_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            self.usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
            self.usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
        self.usage["calls"] += 1

    def get_usage(self) -> dict[str, int]:
        return dict(self.usage)

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        schema: Type[BaseModel],
        *,
        model: str,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self._sem:
                    response = await self._client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        response_format={"type": "json_object"},
                    )
                text = response.choices[0].message.content or "{}"
                data = extract_json(text)
                obj = schema.model_validate(data)
                self._add_usage(response)
                return obj.model_dump()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if "json_object" in str(exc).lower():
                    # Some local servers do not accept response_format; retry without it.
                    try:
                        async with self._sem:
                            response = await self._client.chat.completions.create(
                                model=model,
                                messages=messages,
                                temperature=temperature,
                            )
                        text = response.choices[0].message.content or "{}"
                        data = extract_json(text)
                        obj = schema.model_validate(data)
                        self._add_usage(response)
                        return obj.model_dump()
                    except Exception as retry_exc:  # noqa: BLE001
                        last_error = retry_exc
                continue
        raise RuntimeError(f"LLM JSON completion failed after retries: {last_error}")

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.7,
    ) -> str:
        async with self._sem:
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
        self._add_usage(response)
        return response.choices[0].message.content or ""


class FakeLLM:
    """Scripted LLM for offline tests and demos."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self.responses: dict[str, Any] = responses or {}
        self.calls: list[dict[str, Any]] = []
        self.usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }

    def get_usage(self) -> dict[str, int]:
        return dict(self.usage)

    async def complete_json(self, messages, schema, *, model="fake", temperature=0.2):
        self.calls.append({"kind": "json", "messages": messages, "model": model})
        self.usage["calls"] += 1
        # Match by a simple key from the last user/system content.
        key = self._key(messages)
        data = self.responses.get(key)
        if data is None:
            data = self.responses.get("*", {})
        obj = schema.model_validate(data)
        return obj.model_dump()

    async def complete_text(self, messages, *, model="fake", temperature=0.7):
        self.calls.append({"kind": "text", "messages": messages, "model": model})
        self.usage["calls"] += 1
        key = self._key(messages)
        text = self.responses.get(key)
        if text is None:
            text = self.responses.get("*", "（正文占位）")
        return text

    @staticmethod
    def _key(messages: list[dict[str, str]]) -> str:
        for msg in reversed(messages):
            if msg.get("role") in {"user", "system"}:
                content = msg.get("content", "")
                if len(content) > 80:
                    return content[:80]
                if content:
                    return content
        return "*"