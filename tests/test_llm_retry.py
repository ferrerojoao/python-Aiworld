"""LLM 网关的超时与重试策略（TDD §9「超时与重试必须可预测」）。

全部离线——把 client.chat.completions.create 换成 stub，不联网。
覆盖：SDK 自带重试已关闭 + 分级超时；不可重试错误立即抛且只发 1 次请求；
可重试错误的退避序列 [1.0, 2.0]；退避后第二次尝试成功；
TraceRecorder 成功与失败都记耗时。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import NotFoundError, RateLimitError
from pydantic import BaseModel

from app.core.llm import _MAX_ATTEMPTS, LLMGateway
from app.runtime.trace import TraceRecorder


class _Schema(BaseModel):
    ok: bool = True


def _response(content: str = '{"ok": true}'):
    """最小可用的 chat.completions 响应对象（够 _add_usage 与取值用）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def _status_error(cls, status: int, message: str = "boom"):
    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


def _gateway_with(create) -> LLMGateway:
    """构造一个把 HTTP 调用替换成 `create` 的网关（不联网）。"""
    gateway = LLMGateway("http://127.0.0.1:9/v1", "k", timeout=90)
    gateway._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return gateway


def _ask(gateway: LLMGateway):
    return asyncio.run(
        gateway.complete_json([{"role": "user", "content": "hi"}], _Schema, model="m")
    )


def test_sdk_retries_disabled_and_tiered_timeout():
    """SDK 隐形重试必须关掉，超时必须分级（read 取传入秒数）。"""
    gateway = LLMGateway("http://127.0.0.1:9/v1", "k", timeout=90)
    assert gateway._client.max_retries == 0
    timeout = gateway._client.timeout
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 90, 30, 10)


def test_fatal_error_raises_immediately_without_retry():
    """404（模型名写错）属配置错误：只发 1 次请求，立即抛并把原文带出去。"""
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise _status_error(NotFoundError, 404, "model not found")

    gateway = _gateway_with(create)
    with pytest.raises(RuntimeError) as excinfo:
        _ask(gateway)
    assert len(calls) == 1
    assert "不可重试" in str(excinfo.value)


def test_retryable_error_backs_off_1s_then_2s(monkeypatch):
    """429 可重试：退避 1s、2s，用满 _MAX_ATTEMPTS 后放弃。"""
    calls: list[dict] = []
    sleeps: list[float] = []

    async def create(**kwargs):
        calls.append(kwargs)
        raise _status_error(RateLimitError, 429, "slow down")

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    gateway = _gateway_with(create)
    with pytest.raises(RuntimeError):
        _ask(gateway)
    assert sleeps == [1.0, 2.0]
    assert len(calls) == _MAX_ATTEMPTS


def test_succeeds_on_second_attempt_after_retryable_error(monkeypatch):
    """可重试错误退避后第二次成功：不再继续尝试，直接返回结果。"""
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _status_error(RateLimitError, 429, "slow down")
        return _response()

    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    gateway = _gateway_with(create)
    data = _ask(gateway)
    assert data == {"ok": True}
    assert len(calls) == 2


def test_trace_recorder_records_duration_on_success():
    """成功的一笔要带 started_at / duration_ms。"""
    from app.core.llm import FakeLLM

    recorder = TraceRecorder(FakeLLM({"*": {"ok": True}}))
    asyncio.run(
        recorder.complete_json([{"role": "user", "content": "hi"}], _Schema, model="m")
    )
    entry = recorder.entries[0]
    assert entry["started_at"] > 0
    assert isinstance(entry["duration_ms"], int)
    assert entry["duration_ms"] >= 0
    assert "output" in entry and "error" not in entry


def test_trace_recorder_records_duration_on_failure():
    """失败的一笔也要记耗时——卡住的调用正是最该看到耗时的那种。"""

    class _Boom:
        async def complete_json(self, *args, **kwargs):
            raise RuntimeError("boom")

    recorder = TraceRecorder(_Boom())
    with pytest.raises(RuntimeError):
        asyncio.run(
            recorder.complete_json([{"role": "user", "content": "hi"}], _Schema, model="m")
        )
    entry = recorder.entries[0]
    assert isinstance(entry["duration_ms"], int)
    assert entry["duration_ms"] >= 0
    assert "error" in entry
