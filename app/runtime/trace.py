from __future__ import annotations

import time
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

    ``worker``（角色）自 2026-09-20 起是本层的转发参数：调用方只报角色，模型名
    与**出口**（主 / 辅两套 base_url）由路由层一处判定。调试卡片必须显示出
    "实际用了哪个模型、敲的哪个门"——双出口配置搞错时，日志里是唯一能看出来的
    地方（这正是本类存在的理由：把看不见的东西变可见）。
    """

    def __init__(self, llm):
        self._llm = llm
        self.entries: list[dict[str, Any]] = []

    def get_usage(self) -> dict[str, int]:
        if hasattr(self._llm, "get_usage"):
            return self._llm.get_usage()
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    def _describe(self, model: str, worker: str) -> tuple[str, str]:
        """补全展示用的 (模型名, 出口标签)。

        拿不到判定就原样返回（测试里的 FakeLLM 没有 resolve —— 它是纯记账的
        假对象，不该为调试信息反向要求它长出路由能力）。
        """
        resolve = getattr(self._llm, "resolve", None)
        if not callable(resolve):
            return model, ""
        try:
            got_model, endpoint = resolve(worker)
        except Exception:  # noqa: BLE001 - 诊断信息不该影响主流程
            return model, ""
        return model or got_model, endpoint

    async def complete_json(self, messages, schema, *, model="", temperature=0.2, worker=""):
        model, endpoint = self._describe(model, worker)
        entry: dict[str, Any] = {
            "type": "json",
            "label": _label_for(schema),
            "model": model,
            "worker": worker,
            "endpoint": endpoint,
            "temperature": temperature,
            "messages": messages,
            "started_at": time.time(),
        }
        try:
            result = await self._llm.complete_json(
                messages, schema, model=model, temperature=temperature, worker=worker
            )
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        else:
            entry["output"] = result
            return result
        finally:
            # 失败的那一笔也必须记耗时——卡住的调用恰恰是最需要看到耗时的那种。
            entry["duration_ms"] = int((time.time() - entry["started_at"]) * 1000)
            self.entries.append(entry)

    async def complete_text(self, messages, *, model="", temperature=0.7, worker=""):
        model, endpoint = self._describe(model, worker)
        entry: dict[str, Any] = {
            "type": "text",
            "label": None,
            "model": model,
            "worker": worker,
            "endpoint": endpoint,
            "temperature": temperature,
            "messages": messages,
            "started_at": time.time(),
        }
        try:
            result = await self._llm.complete_text(
                messages, model=model, temperature=temperature, worker=worker
            )
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        else:
            entry["output"] = result
            return result
        finally:
            entry["duration_ms"] = int((time.time() - entry["started_at"]) * 1000)
            self.entries.append(entry)
