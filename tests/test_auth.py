from __future__ import annotations

import re
import shutil

import pytest
from fastapi.testclient import TestClient

from app.api.security import auth_rejection, bearer_token, is_local_host
from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT

# 一个明确的"网段内其它机器"地址：走非本机分支，但不是回环。
REMOTE = ("192.168.31.50", 51234)
LOCAL = ("127.0.0.1", 51234)

TOKEN = "s3cret-token"


def _make_client(tmp_path, client_addr, *, with_world: bool = False, **overrides):
    """装配一个测试 app。

    ``content_root`` 指到 tmp_path：不碰仓库里真实的 content/（CI 上根本没有），
    也让 lifespan 的世界扫描是确定的空集。
    """
    world_root = tmp_path / "qinghsi"
    if with_world and not world_root.exists():
        shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(
        content_root=tmp_path,
        data_dir=tmp_path / "data",
        candidate_ttl_days=7,
        **overrides,
    )
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    return TestClient(app, client=client_addr)


def _settings(**kw) -> Settings:
    base: dict = {"content_root": ".", "data_dir": "."}
    base.update(kw)
    return Settings(**base)


# --------------------------------------------------------------------------
# 第一层：纯函数（不经过 HTTP，把判定矩阵钉死）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER   abc  ", "abc"),
        ("Basic abc", ""),
        ("abc", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_bearer_token_parsing(header, expected):
    assert bearer_token(header) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("testclient", True),
        (" 127.0.0.1 ", True),
        ("192.168.31.50", False),
        ("10.0.0.9", False),
        (None, False),
        ("", False),
    ],
)
def test_is_local_host(host, expected):
    assert is_local_host(host) is expected


@pytest.mark.parametrize(
    ("require", "token", "path", "host", "authorization", "blocked"),
    [
        # 只护数据面：静态面永远放行
        (True, TOKEN, "/", "192.168.31.50", None, False),
        (True, TOKEN, "/app.js", "192.168.31.50", None, False),
        # 本机永远放行
        (True, TOKEN, "/api/settings", "127.0.0.1", None, False),
        (True, TOKEN, "/api/settings", "testclient", None, False),
        # 非本机 + 要求鉴权 + 有令牌
        (True, TOKEN, "/api/settings", "192.168.31.50", None, True),
        (True, TOKEN, "/api/settings", "192.168.31.50", "Bearer wrong", True),
        (True, TOKEN, "/api/settings", "192.168.31.50", f"Bearer {TOKEN}", False),
        # 非本机 + 要求鉴权 + **没配令牌** = 失败关闭（不是放行）
        (True, "", "/api/settings", "192.168.31.50", None, True),
        (True, "", "/api/settings", "192.168.31.50", "Bearer anything", True),
        # 关掉开关 = 一律放行（C 方案：绑全网卡 + 防火墙限源网段）
        (False, "", "/api/settings", "192.168.31.50", None, False),
        (False, TOKEN, "/api/settings", "192.168.31.50", None, False),
    ],
)
def test_auth_rejection_matrix(require, token, path, host, authorization, blocked):
    settings = _settings(require_auth_for_non_local=require, auth_token=token)
    reason = auth_rejection(settings, path, authorization, host)
    assert (reason is not None) is blocked, f"path={path} host={host} auth={authorization!r} → {reason!r}"


def test_missing_token_reason_tells_you_what_to_do():
    """失败理由必须能直接读出来该怎么修——这是唯一的排障线索。"""
    settings = _settings(require_auth_for_non_local=True, auth_token="")
    reason = auth_rejection(settings, "/api/settings", None, "192.168.31.50")
    assert reason and "AIWORLD_AUTH_TOKEN" in reason


def test_non_ascii_token_does_not_explode():
    """token 里若有非 ASCII 字符，str 版 compare_digest 会抛 TypeError。"""
    settings = _settings(require_auth_for_non_local=True, auth_token="令牌·中文")
    assert auth_rejection(settings, "/api/settings", "Bearer 令牌·中文", "192.168.31.50") is None
    assert auth_rejection(settings, "/api/settings", "Bearer 令牌·中文二", "192.168.31.50") is not None


# --------------------------------------------------------------------------
# 第二层：走真实 HTTP（中间件真的接上了吗）
# --------------------------------------------------------------------------


def test_remote_client_without_token_gets_401(tmp_path):
    with _make_client(tmp_path, REMOTE, require_auth_for_non_local=True, auth_token=TOKEN) as client:
        r = client.get("/api/settings")
        assert r.status_code == 401, r.text
        assert "Bearer" in r.headers.get("www-authenticate", "")
        assert "缺少访问令牌" in r.json()["detail"]


def test_remote_client_with_correct_token_is_allowed(tmp_path):
    with _make_client(tmp_path, REMOTE, require_auth_for_non_local=True, auth_token=TOKEN) as client:
        r = client.get("/api/settings", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text


def test_remote_client_with_wrong_token_gets_401(tmp_path):
    with _make_client(tmp_path, REMOTE, require_auth_for_non_local=True, auth_token=TOKEN) as client:
        r = client.get("/api/settings", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401
        assert "不正确" in r.json()["detail"]


def test_local_client_needs_no_token(tmp_path):
    with _make_client(tmp_path, LOCAL, require_auth_for_non_local=True, auth_token=TOKEN) as client:
        assert client.get("/api/settings").status_code == 200


def test_opt_out_keeps_current_deployment_working(tmp_path):
    """C 方案（绑 0.0.0.0 + 关掉鉴权）不能被这次改动回退掉。"""
    with _make_client(tmp_path, REMOTE, require_auth_for_non_local=False, auth_token="") as client:
        assert client.get("/api/settings").status_code == 200


def test_static_frontend_is_not_behind_the_gate(tmp_path):
    """静态面必须裸奔：浏览器取 <script src> 时带不了 Authorization 头。"""
    with _make_client(tmp_path, REMOTE, require_auth_for_non_local=True, auth_token=TOKEN) as client:
        assert client.get("/").status_code == 200


def test_sse_stream_survives_the_auth_middleware(tmp_path):
    """非本机 + 令牌下跑完整一轮，确认中间件没把回合 SSE 弄坏。

    这是纯 ASGI 中间件（而非 BaseHTTPMiddleware）的验收点：后者会把响应包一层，
    对流式响应有额外缓冲/取消语义。TestClient 仍会缓冲整条流，所以这里验的是
    "事件没被吞、顺序不变"，实时性另论。
    """
    with _make_client(
        tmp_path, REMOTE, with_world=True, require_auth_for_non_local=True, auth_token=TOKEN
    ) as client:
        auth = {"Authorization": f"Bearer {TOKEN}"}

        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}, headers=auth)
        assert r.status_code == 200, r.text
        sid = r.json()["sid"]

        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "去网吧找朱明"}, headers=auth)
        assert r.status_code == 200, r.text
        events = re.findall(r"^event: (\w+)", r.text, flags=re.MULTILINE)
        assert events, r.text
        assert events[-1] == "candidate", f"终态事件必须压轴：{events}"


def test_sse_turn_is_blocked_without_token(tmp_path):
    """同一轮，没带令牌就必须在进业务逻辑之前被挡住。"""
    with _make_client(
        tmp_path, REMOTE, with_world=True, require_auth_for_non_local=True, auth_token=TOKEN
    ) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        assert r.status_code == 401
