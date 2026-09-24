import shutil

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT


def test_root_and_world_listing(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app) as client:
        assert "AIWorld" in client.get("/").text
        worlds = client.get("/api/worlds").json()["worlds"]
        assert worlds and worlds[0]["id"] == "qinghsi"


def test_restores_sessions_after_restart(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app) as client:
        client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})

    app2 = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app2) as client2:
        sessions = client2.get("/api/sessions").json()["sessions"]
        assert any(s["save_name"] == "main" for s in sessions)


def test_open_existing_session(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()
        opened = client.post(
            "/api/sessions/open", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()
        assert opened["sid"] == created["sid"]


# ---------------------------------------------------------------------------
# 前端资源的缓存口径（2026-09-24「换了新包看到的还是旧界面」）
# ---------------------------------------------------------------------------
#
# 事故形态：新包的后端确实在跑（启动 banner 的 Build 是新提交），界面却是旧前端
# （徽标 `前端 v75`、还带早已删掉的「数值轴」页签）。原因不在代码、不在包，在响应头：
# `GET /` 一个 `Cache-Control` 都不发 ⇒ 浏览器按**启发式新鲜度**（约文件年龄的 10%）
# 直接吃旧 index.html，而旧 HTML 里钉的是 `app.js?v=75` ⇒ 整个前端锁死在旧版。
#
# 这两条是**一对**，缺一不可：入口页每次重取，才能保证它钉的 `?v=` 是新的；
# 带 `?v=` 的资源才敢长期 immutable。只留后一半 = 改了前端忘了跳号也发不出去。


def _static_client(tmp_path) -> TestClient:
    app = create_app(
        settings=Settings(content_root=tmp_path, candidate_ttl_days=7), llm=FakeLLM({})
    )
    return TestClient(app)


def test_entry_page_is_never_cached(tmp_path):
    """`/` 与 `/index.html` 都必须 `no-store`：旧一份入口页 = 整个前端锁死在旧版。"""
    with _static_client(tmp_path) as client:
        root = client.get("/")
        assert root.headers["content-type"].startswith("text/html"), (
            "这条守的是入口页，不是那个 JSON 兜底 —— 断言必须先确认真发了 HTML"
        )
        assert "no-store" in root.headers.get("cache-control", ""), (
            "入口页不带 no-store ⇒ 换包之后浏览器会继续吃旧 index.html，"
            "而它钉着旧的 app.js?v=…，界面就再也换不过来了"
        )

        # 静态挂载侧那条路（/index.html）走的是 `_StaticAssets` 的 html 分支，
        # 与上一条是两个入口，都要守。
        direct = client.get("/index.html")
        assert "no-store" in direct.headers.get("cache-control", "")


def test_versioned_assets_are_immutable(tmp_path):
    """`?v=<内容哈希>` 是内容寻址 URL ⇒ 同一 URL 永远同一份内容，可以 long-lived immutable。"""
    with _static_client(tmp_path) as client:
        r = client.get("/app.js")
        assert r.status_code == 200
        assert not r.headers["content-type"].startswith("text/html"), "别把资源当成入口页判"
        cc = r.headers.get("cache-control", "")
        assert "immutable" in cc and "max-age=31536000" in cc, (
            "带 ?v= 的资源应当不可变缓存；`?v=` 由 scripts/bump_frontend_version.py 写、"
            "tests/test_frontend_version.py 守着它等于内容哈希"
        )