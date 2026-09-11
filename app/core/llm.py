from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Type

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, Timeout
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 超时与重试策略（TDD §9「超时与重试必须可预测」）
#
# 旧实现的三层乘法：complete_json 外层 3 次 × SDK 隐形 3 次 × 120s ≈ 18 分钟；
# 且 `except Exception` 全捕获，连 404（模型名写错）也白等重试。策略现在单点
# 收敛到本模块——SDK 自带重试关闭，网关自己分流可重试 / 不可重试。
# ---------------------------------------------------------------------------

# complete_json 外层尝试上限（结构降级与退避重试共用一个循环）
_MAX_ATTEMPTS = 3

# 可重试错误的退避序列：第 1 次失败等 1s、第 2 次等 2s；再失败就放弃。
_RETRY_BACKOFF = (1.0, 2.0)

# 可重试 = 网络层超时/连接失败（无 status_code），以及网关侧 429 与 5xx。
_RETRYABLE_STATUS = frozenset({429})
# 不可重试（配置 / 调用方错误，重试只是浪费——把模型原文直接抛出去）：
# 400 参数错、401 鉴权失败、403 无权限、404 模型名写错、422 结构被拒。
_FATAL_STATUS = frozenset({400, 401, 403, 404, 422})

_RETRYABLE_ERRORS = (asyncio.TimeoutError, TimeoutError, APITimeoutError, APIConnectionError)


def _status_of(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _is_retryable(exc: BaseException) -> bool:
    """超时 / 连接失败 / 429 / 5xx → True；其余（含 fatal 与本地上层错误）→ False。"""
    status = _status_of(exc)
    if status is not None:
        return status in _RETRYABLE_STATUS or status >= 500
    # 无状态码：openai 的超时/连接异常不带 status_code，按类型判定。
    return isinstance(exc, _RETRYABLE_ERRORS)


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

    def __init__(
        self,
        base_url: str,
        api_key: str,
        max_concurrency: int = 4,
        timeout: float = 90,
        reasoning_effort: str = "",
    ):
        # max_retries=0：关掉 SDK 隐形重试（默认 2 → 单次调用实际发 3 次 HTTP）。
        # 开着的话"一次失败的真实代价 = 网关重试 × SDK 重试"，完全不可预测。
        #
        # timeout 必须用 SDK 导出的 openai.Timeout（实测就是 httpx2.Timeout）：
        # 传普通 httpx.Timeout 构造时不报错、client.timeout 也读得出来，
        # 真正发请求时才抛 TypeError: unhashable type: 'Timeout'。
        # 分级：连接 10s / 读取取 AIWORLD_LLM_TIMEOUT_SECONDS / 写 30s / 池 10s。
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=Timeout(connect=10, read=timeout, write=30, pool=10),
            max_retries=0,
        )
        self._sem = asyncio.Semaphore(max_concurrency)
        self.reasoning_effort = reasoning_effort.lower()
        self.usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }

    def _effort_kwargs(self) -> dict[str, Any]:
        # Only forward reasoning_effort when a level is set; downgrade to
        # silent (no param) if the endpoint rejects it.
        return {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}

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
        last_raw: str | None = None
        use_response_format = True
        retry_index = 0
        for attempt in range(_MAX_ATTEMPTS):
            msgs = list(messages)
            if attempt > 0 and last_raw:
                # Feedback loop: show the model its previous bad output and
                # the validation error so it can correct the shape.
                msgs.append({"role": "assistant", "content": last_raw})
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            "你上次的输出未通过校验，请修正后重新输出。"
                            f"必须只返回能通过校验的 JSON。上次输出：{last_raw[:500]}"
                        ),
                    }
                )
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": msgs,
                "temperature": temperature,
            }
            kwargs.update(self._effort_kwargs())
            if use_response_format:
                kwargs["response_format"] = {"type": "json_object"}
            text: str | None = None
            try:
                async with self._sem:
                    response = await self._client.chat.completions.create(**kwargs)
                text = response.choices[0].message.content or "{}"
                data = extract_json(text)
                obj = schema.model_validate(data)
                self._add_usage(response)
                return obj.model_dump()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if text:
                    last_raw = text
                lowered = str(exc).lower()
                # ① 结构降级优先于 fatal 判定：网关拒绝 response_format /
                # reasoning_effort 时回的也是 400，按 fatal 直接放弃就错了。
                if "reasoning_effort" in lowered:
                    # Endpoint does not support a reasoning level; stop
                    # forwarding it for the remaining attempts.
                    self.reasoning_effort = ""
                    continue
                if "json_object" in lowered:
                    # Some local servers do not accept response_format; retry
                    # the same attempt shape without it.
                    use_response_format = False
                    continue
                # ② 本地解析 / 校验失败（extract_json 抛的 ValueError、Pydantic 抛的
                # ValidationError 都是 ValueError）：不是 HTTP 错误，带原文反馈重试。
                if isinstance(exc, ValueError):
                    continue
                # ③ 不可重试：配置 / 调用方错误（400/401/403/404/422），立即抛出
                # 并把模型原文带出去——重试只是白等。
                if not _is_retryable(exc):
                    raise RuntimeError(
                        f"LLM 调用失败（不可重试）：{type(exc).__name__}: {exc}"
                        + (f" | raw: {last_raw[:200]}" if last_raw else "")
                    ) from exc
                # ④ 可重试：退避 1s / 2s 后进入下一轮；已是最后一轮就不等了。
                if retry_index < len(_RETRY_BACKOFF) and attempt + 1 < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF[retry_index])
                    retry_index += 1
        raise RuntimeError(
            f"LLM JSON completion failed after retries: {last_error}"
            + (f" | raw: {last_raw[:200]}" if last_raw else "")
        )

    async def complete_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.7,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        kwargs.update(self._effort_kwargs())
        try:
            async with self._sem:
                response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if "reasoning_effort" in str(exc).lower():
                self.reasoning_effort = ""
                async with self._sem:
                    response = await self._client.chat.completions.create(
                        model=model, messages=messages, temperature=temperature
                    )
            else:
                raise
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