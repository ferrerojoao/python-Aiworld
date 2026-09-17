"""content/ 不存在或为空时，主程序必须照常工作。

content/ 被 .gitignore 整目录排除（运行态世界与存档），所以任一克隆或便携包里
都可能根本没有这个目录。这组测试锁住四件事：

1. 启动（lifespan）不炸；
2. 世界列表返回空数组，而不是报错；
3. 对不存在的世界给干净的 404，而不是 500；
4. 能从零建世界、开一局、读状态——且 content/ 目录被自动创建。
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import GENERIC_LLM_RESPONSE


def _client(content_root):
    app = create_app(
        settings=Settings(content_root=content_root, candidate_ttl_days=7),
        llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}),
    )
    return TestClient(app)


def _assert_empty_then_buildable(client, content_root) -> None:
    assert client.get("/api/worlds").json()["worlds"] == []
    assert client.get("/api/sessions").json()["sessions"] == []
    assert client.get("/api/settings").status_code == 200

    # 空目录下点一个不存在的世界：干净 404
    assert client.get("/api/worlds/ghost").status_code == 404
    assert client.post(
        "/api/sessions", json={"world_id": "ghost", "save_name": "main"}
    ).status_code == 404

    # 从零建世界 → content/ 自动创建 → 可开局
    created = client.post(
        "/api/worlds/new",
        json={"world_id": "fresh", "name": "苟活", "player_name": "阿星", "start_scene": "主街"},
    )
    assert created.status_code == 200, created.text
    assert content_root.is_dir()
    assert [w["id"] for w in client.get("/api/worlds").json()["worlds"]] == ["fresh"]

    opened = client.post("/api/sessions", json={"world_id": "fresh", "save_name": "main"})
    assert opened.status_code == 200, opened.text
    sid = opened.json()["sid"]
    assert client.get(f"/api/sessions/{sid}/state").status_code == 200
    assert client.get(f"/api/sessions/{sid}/ledger/events").status_code == 200
    assert client.get(f"/api/sessions/{sid}/candidates/pending").status_code == 200


def test_missing_content_dir_is_usable(tmp_path):
    """content/ 整个不存在（新克隆的样子）。"""
    content_root = tmp_path / "content"
    assert not content_root.exists()
    with _client(content_root) as client:
        _assert_empty_then_buildable(client, content_root)


def test_empty_content_dir_is_usable(tmp_path):
    """content/ 存在但一个世界都没有。"""
    content_root = tmp_path / "content"
    content_root.mkdir()
    with _client(content_root) as client:
        _assert_empty_then_buildable(client, content_root)
