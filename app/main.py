from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_director import router as director_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_turn import router as turn_router
from app.config import Settings, get_settings
from app.core.llm import FakeLLM, LLMGateway
from app.core.presets import load_global_preset
from app.runtime.session import GameSession, open_session
from app.runtime.turn import TurnRunner

_FAKE_RESPONSE = {
    "mode": "scene",
    "beats": [{"kind": "narrate", "text": "朱明从网吧出来，看见你愣了一下。"}],
    "lore_refs": [],
    "motivation_note": "",
    "adopt_player_body": False,
    "private": False,
    "location": "main_street",
    "participants": ["player", "npc_zhuming"],
    "prose": "朱明从网吧出来，看见你愣了一下，把烟头踩灭，问你吃饭了没。",
    "time_hint": None,
    "status": "pass",
    "issues": [],
    "decision": "打哈哈",
    "action_hint": "拉你去吃面",
    "tone": "随意",
    "memo": "他不想提昨天打架的事。",
    "hook_texts": [],
    "conflicts": [],
}


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.sessions: dict[str, GameSession] = {}
        app.state.global_preset = load_global_preset(settings.data_dir / "presets.json")
        # Restore existing saves so the API can list/open them after restart.
        content_root = Path(settings.content_root)
        if content_root.is_dir():
            for world_dir in content_root.glob("*"):
                if not (world_dir / "world.json").exists():
                    continue
                save_root = world_dir / "saves"
                if not save_root.is_dir():
                    continue
                for save_dir in save_root.iterdir():
                    if (save_dir / "save.json").exists():
                        try:
                            session = open_session(world_dir, save_root, save_dir.name)
                            app.state.sessions[session.sid] = session
                        except Exception:
                            continue

        if llm is not None:
            app.state.llm = llm
        elif settings.fake_llm:
            app.state.llm = FakeLLM({"*": _FAKE_RESPONSE})
        else:
            app.state.llm = LLMGateway(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                max_concurrency=4,
            )

        def factory(session: GameSession) -> TurnRunner:
            return TurnRunner(session, app.state.llm, settings, preset=app.state.global_preset)

        app.state.turn_runner_factory = factory
        yield

    app = FastAPI(title="AIWorld", lifespan=lifespan)
    app.include_router(sessions_router)
    app.include_router(turn_router)
    app.include_router(director_router)

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