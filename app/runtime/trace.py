from __future__ import annotations

from typing import Any

# schema 类型 → 调用者身份（调试卡片头显示用）
_SCHEMA_LABELS = {
    "WriterOutput": "写手",
    "ActorDecision": "Actor",
    "QCOutput": "质检",
    "AuditOutput": "审计",
    "DirectorReply": "导演",
}


def _label_for(schema: Any) -> str | None:
    name = getattr(schema, "__name__", None)
    return _SCHEMA_LABELS.get(name, name)


class TraceRecorder:
    """Wrap an LLM and record every call for debugging.

    Each entry carries a `label` (derived from the output schema, e.g.
    WriterOutput → 写手) so the debug panel can render one readable card per
    call. Recording is transparent: callers pass the recorder anywhere a
    plain LLM gateway is accepted.
    """

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
            "label": _label_for(schema),
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
            "label": None,
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
