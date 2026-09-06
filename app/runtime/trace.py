from __future__ import annotations

from typing import Any


class TraceRecorder:
    """Wrap an LLM and record every call for debugging."""

    def __init__(self, llm):
        self._llm = llm
        self.entries: list[dict[str, Any]] = []

    def get_usage(self) -> dict[str, int]:
        if hasattr(self._llm, "get_usage"):
            return self._llm.get_usage()
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    async def complete_json(self, messages, schema, *, model="fake", temperature=0.2):
        entry: dict[str, Any] = {
            "type": "json",
            "model": model,
            "temperature": temperature,
            "messages": messages,
        }
        try:
            result = await self._llm.complete_json(
                messages, schema, model=model, temperature=temperature
            )
            entry["output"] = result
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
            self.entries.append(entry)
            raise
        self.entries.append(entry)
        return result

    async def complete_text(self, messages, *, model="fake", temperature=0.7):
        entry: dict[str, Any] = {
            "type": "text",
            "model": model,
            "temperature": temperature,
            "messages": messages,
        }
        try:
            result = await self._llm.complete_text(
                messages, model=model, temperature=temperature
            )
            entry["output"] = result
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
            self.entries.append(entry)
            raise
        self.entries.append(entry)
        return result