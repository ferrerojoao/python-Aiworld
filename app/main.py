from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
from app.core.llm import LLMGateway
from app.core.presets import load_global_preset
from app.runtime.session import GameSession, open_session
from app.runtime.turn import TurnRunner


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
            app.state.llm = LLMGateway(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                max_concurrency=4,
                timeout=settings.llm_timeout_seconds,
                reasoning_effort=settings.reasoning_effort,
            )

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
            return FileResponse(_index)
        return JSONResponse(
            {
                "name": "AIWorld",
                "docs": "docs/",
                "api": "/api/worlds",
                "status": "ok",
            }
        )

    if _web_dist.is_dir():
        app.mount("/", StaticFiles(directory=_web_dist, html=True), name="web")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()