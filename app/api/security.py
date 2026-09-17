"""请求级访问控制（P1-4）。

背景：``AIWORLD_AUTH_TOKEN`` 此前只是**启动期的一个空壳**——``check_host_security()``
会在"绑非回环地址 + 没配 token"时拒绝启动，但全仓没有任何**请求级**校验。
只要配了 token（或者干脆按 C 方案把 REQUIRE_AUTH_FOR_NON_LOCAL 关掉），
网段内任何人都能读改世界数据、并借这台机器的 LLM 额度。

这里补的是请求级那一半。

口径（三条，缺一不可，与 ``Settings`` 里的字段一一对应）：
1. **只护数据面**：``/api/*``。静态面（index.html / app.js / style.css）必须裸奔——
   浏览器取 ``<script src>`` 时带不了 Authorization 头，护住它等于连页面都打不开。
2. **只护非本机**：回环地址 + TestClient 一律放行，本机单人使用不需要令牌。
3. **本机之外一律失败关闭**：要求鉴权却没配 token 时**拒绝**（不是放行）。
   ``check_host_security()`` 已在启动期拦掉这种组合，这里是它漏网时的第二道闸。

⚠️ 实现用**纯 ASGI 中间件**，不用 ``@app.middleware("http")``：
Starlette 的 ``BaseHTTPMiddleware`` 会把响应包一层，对 ``StreamingResponse``
（本项目的回合 SSE）有额外的缓冲/取消语义风险。纯 ASGI 直接把 ``send`` 透传，
对流式响应零影响——这是这个项目里唯一安全的接法。
"""

from __future__ import annotations

import hmac
from typing import Any

from app.config import Settings

#: 受保护前缀。所有数据路由都在 ``/api`` 下；不在这个前缀下的新路由**不受保护**，
#: 所以新增数据接口务必放 ``/api``。
PROTECTED_PREFIX = "/api"

#: "本机"判定白名单。``testclient`` 是 Starlette TestClient 的默认 client.host
#: （其默认 client 为 ``("testclient", 50000)``），测试里等同回环。
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def is_local_host(host: str | None) -> bool:
    return bool(host) and host.strip().lower() in LOCAL_HOSTS


def bearer_token(authorization: str | None) -> str:
    """从 ``Authorization`` 头里取出 Bearer 令牌；形状不对返回空串。"""
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.strip().lower() != "bearer":
        return ""
    return value.strip()


def auth_rejection(
    settings: Settings,
    path: str,
    authorization: str | None,
    client_host: str | None,
) -> str | None:
    """返回 ``None`` = 放行；返回字符串 = 拒绝理由（直接作为 401 的 detail）。

    纯函数，不碰 request 对象——方便单测把四种组合一次性钉死。
    """
    if not path.startswith(PROTECTED_PREFIX):
        return None
    if not settings.require_auth_for_non_local:
        return None
    if is_local_host(client_host):
        return None

    if not settings.auth_token:
        return (
            "服务端未配置 AIWORLD_AUTH_TOKEN，拒绝非本机访问。"
            "请在服务端的 .env 里设置令牌后重启，或改绑 127.0.0.1。"
        )

    supplied = bearer_token(authorization)
    if not supplied:
        return "缺少访问令牌：请带上 Authorization: Bearer <token> 重试。"
    # 比字节而不是比字符串：token 里若有非 ASCII 字符，str 版会直接抛 TypeError。
    if not hmac.compare_digest(supplied.encode("utf-8"), settings.auth_token.encode("utf-8")):
        return "访问令牌不正确。"
    return None


class TokenAuthMiddleware:
    """纯 ASGI 中间件：数据面非本机访问要求 Bearer 令牌。"""

    def __init__(self, app: Any, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0] if client else None
        headers = {k.lower(): v for k, v in scope.get("headers") or []}
        raw_auth = headers.get(b"authorization")
        reason = auth_rejection(
            self.settings,
            scope.get("path", ""),
            raw_auth.decode("latin-1") if raw_auth else None,
            client_host,
        )
        if reason is not None:
            await _send_401(send, reason)
            return

        await self.app(scope, receive, send)


async def _send_401(send: Any, reason: str) -> None:
    import json

    body = json.dumps({"detail": reason}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b'Bearer realm="AIWorld"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
