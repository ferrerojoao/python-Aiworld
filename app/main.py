from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.routes_director import router as director_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_turn import router as turn_router
from app.api.security import TokenAuthMiddleware
from app.config import (
    Settings,
    apply_settings_overrides,
    get_settings,
    load_settings_overrides,
)
from app.core.llm import LLMRouter
from app.core.presets import load_global_preset
from app.runtime.session import GameSession, open_session
from app.runtime.turn import TurnRunner


class _StaticAssets(StaticFiles):
    """前端资源挂载：**入口页永不缓存，带 ``?v=`` 的资源长期不可变缓存**。

    🔴 入口页（``index.html``）必须每次完整重取（``no-store``）。它把 ``?v=<内容哈希>``
    钉死在 ``app.js`` / ``style.css`` 上 —— **它旧一份，整个前端就锁死在旧版**：后端明明是新的
    （启动 banner 的 ``Build`` 是新包、界面却还是旧版），2026-09-24 换机就是栽在这里
    （用户看到 ``前端 v75`` 和早已删掉的「数值轴」页签）。

    旧实现一个 ``Cache-Control`` 都不发 ⇒ 浏览器走**启发式新鲜度**（约文件年龄的 10%）
    直接吃缓存：**文件越老窗口越长**（mtime 在四天前的 HTML 能白吃将近十小时），
    换包的这段时间里它压根不发请求 —— 这就是 2026-09-24 的实际形态。

    第二条路更窄、但也真实：**Starlette 的 ``ETag`` 不是内容哈希**，是
    ``md5(str(st_mtime) + "-" + str(st_size))``（``FileResponse.set_stat_headers``），
    而打包用 ``shutil.copytree`` —— **保留源文件的 mtime**（实测确认）⇒ 新旧两个包里同一份
    文件 ``mtime`` 相同，``size`` 只要也凑巧一样，``ETag`` 就一样 ⇒ 一个错的 304 把旧 HTML 发出去
    （``StaticFiles.is_not_modified`` 里 ``if-none-match`` 命中即返回，不再看 ``if-modified-since``）。

    所以这里选 ``no-store`` 而不是 ``no-cache``：``no-cache`` 仍然是"每次重验证"，
    这条重验证的路还开着；``no-store`` 干脆不留副本、也不发条件请求，两个洞一起堵。
    代价是每次开页面多几十 KB —— 本机自用，不值一提。

    ✅ 带 ``?v=`` 的资源反过来**可以** ``immutable``：URL 是内容寻址的（``?v=`` 就是内容哈希，
    由 ``scripts/bump_frontend_version.py`` 写、``tests/test_frontend_version.py`` 守），
    同一 URL 永远对应同一份内容 —— 与 ``routes_sessions.py`` 里附图那套理由完全一样。

    ⚠️ 别用 ``app.add_middleware`` 加统一响应头来干这件事：那是纯 ASGI 层的横切，
    会顺带改到 ``/api/sessions/{sid}/turn`` 的 SSE 首包（见 ``app/api/security.py`` 的教训）。
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # UI 改过的设置（data/settings.json）覆盖 env 默认值，重启不丢。
        overrides = load_settings_overrides(settings.data_dir / "settings.json")
        if overrides:
            apply_settings_overrides(settings, overrides)
        app.state.settings = settings
        app.state.sessions: dict[str, GameSession] = {}
        app.state.global_preset = load_global_preset(settings.data_dir / "presets.json")
        # Restore existing saves so the API can list/open them after restart.
        # 世界=存档 1:1：save.json 就在世界目录本身。
        content_root = Path(settings.content_root)
        if content_root.is_dir():
            for world_dir in content_root.glob("*"):
                if not (world_dir / "world.json").exists():
                    continue
                if not (world_dir / "save.json").exists():
                    continue
                try:
                    session = open_session(world_dir)
                    app.state.sessions[session.sid] = session
                except Exception:  # noqa: BLE001 - 单个世界存档损坏不该拖垮整个启动扫描
                    continue

        if llm is not None:
            app.state.llm = llm
        else:
            # 主 / 辅两个出口（2026-09-20）：辅侧没配 base_url 时只会建一个网关，
            # 与拆分之前完全一致。装配口径见 app/core/llm.LLMRouter。
            app.state.llm = LLMRouter(settings)

        def factory(session: GameSession) -> TurnRunner:
            return TurnRunner(session, app.state.llm, settings, preset=app.state.global_preset)

        app.state.turn_runner_factory = factory
        yield

    app = FastAPI(title="AIWorld", lifespan=lifespan)
    app.include_router(sessions_router)
    app.include_router(turn_router)
    app.include_router(director_router)

    # 请求级访问控制（P1-4）：非本机访问 /api/* 需要 Bearer 令牌。
    # 用纯 ASGI 中间件（非 BaseHTTPMiddleware），避免给回合 SSE 加一层缓冲——
    # 判定口径与豁免范围见 app/api/security.py 的模块 docstring。
    app.add_middleware(TokenAuthMiddleware, settings=settings)

    # Serve a built frontend if present; the repo can be run API-only otherwise.
    _web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"

    @app.get("/")
    async def root():
        _index = _web_dist / "index.html"
        if _index.is_file():
            # 🔴 入口页永不缓存：它钉着 ?v=<内容哈希>，旧一份 = 整个前端锁死在旧版。
            #    静态挂载里那份 index.html 由 _StaticAssets 按 text/html 同样处理。
            return FileResponse(_index, headers={"Cache-Control": "no-store"})
        return JSONResponse(
            {
                "name": "AIWorld",
                "docs": "docs/",
                "api": "/api/worlds",
                "status": "ok",
            }
        )

    if _web_dist.is_dir():
        app.mount("/", _StaticAssets(directory=_web_dist, html=True), name="web")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()