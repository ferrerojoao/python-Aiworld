from __future__ import annotations

import base64
import io
import json
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.presets import save_global_preset
from app.runtime.session import create_session, open_session
from app.world.loader import save_world_assets

router = APIRouter(prefix="/api")

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
    "hook_texts": [],
    "conflicts": [],
}


class CreateSessionBody(BaseModel):
    world_id: str
    save_name: str


class OpenSessionBody(BaseModel):
    world_id: str
    save_name: str


class ImportWorldBody(BaseModel):
    filename: str = "world.zip"
    content: str  # base64-encoded zip


class WorldAssetData(BaseModel):
    overview: dict = {}
    lorebook: list = []
    scenes: list = []
    npcs: dict = {}
    axes: list = []


class SaveAsWorldBody(WorldAssetData):
    new_world_id: str


def _world_root(request: Request, world_id: str) -> Path:
    root = Path(request.app.state.settings.content_root) / world_id
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"world not found: {world_id}")
    return root


def _save_root(request: Request, world_id: str) -> Path:
    return _world_root(request, world_id) / "saves"


def _get_session(request: Request, sid: str):
    session = request.app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    return session


@router.get("/worlds")
async def list_worlds(request: Request):
    content_root = Path(request.app.state.settings.content_root)
    worlds = []
    for path in sorted(content_root.glob("*")):
        if path.is_dir() and (path / "world.json").exists():
            worlds.append({"id": path.name, "name": path.name})
    return {"worlds": worlds}


@router.get("/worlds/{world_id}")
async def get_world(request: Request, world_id: str):
    world_root = _world_root(request, world_id)
    save_root = _save_root(request, world_id)
    saves = []
    if save_root.is_dir():
        saves = sorted(
            p.name for p in save_root.iterdir() if (p / "save.json").exists()
        )
    return {"id": world_id, "saves": saves}


@router.delete("/worlds/{world_id}")
async def delete_world(request: Request, world_id: str):
    world_root = _world_root(request, world_id)
    # Remove any loaded sessions belonging to this world.
    for sid, session in list(request.app.state.sessions.items()):
        if session.world.meta.id == world_id:
            del request.app.state.sessions[sid]
    shutil.rmtree(world_root)
    return {"ok": True}


@router.put("/worlds/{world_id}")
async def update_world_assets(request: Request, world_id: str, body: WorldAssetData):
    world_root = _world_root(request, world_id)
    save_root = world_root / "saves"
    has_saves = save_root.is_dir() and any((save_root / p).joinpath("save.json").exists() for p in save_root.iterdir())
    if has_saves:
        raise HTTPException(
            status_code=409,
            detail="该世界已有存档，不能原地覆盖；请使用“另存为新世界”。",
        )
    payload = body.model_dump()
    payload["overview"]["id"] = world_id
    save_world_assets(world_root, payload)
    return {"ok": True}


@router.post("/worlds/{world_id}/save-as")
async def save_as_world(request: Request, world_id: str, body: SaveAsWorldBody):
    # Ensure the source world exists.
    _world_root(request, world_id)
    new_id = body.new_world_id.strip()
    if not new_id:
        raise HTTPException(status_code=400, detail="new_world_id is required")
    content_root = Path(request.app.state.settings.content_root)
    dest = content_root / new_id
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"world already exists: {new_id}")
    payload = body.model_dump()
    payload["overview"]["id"] = new_id
    payload["overview"]["name"] = payload["overview"].get("name") or new_id
    save_world_assets(dest, payload)
    return {"ok": True, "world_id": new_id}


@router.get("/sessions")
async def list_sessions(request: Request):
    sessions = []
    for sid, session in request.app.state.sessions.items():
        sessions.append(
            {
                "sid": sid,
                "world": session.world.meta.id,
                "save_name": session.ledger.save.meta.save_name,
                "clock": session.ledger.save.clock,
            }
        )
    return {"sessions": sessions}


@router.post("/sessions/open")
async def open_existing_session(request: Request, body: OpenSessionBody):
    world_root = _world_root(request, body.world_id)
    save_root = _save_root(request, body.world_id)
    try:
        session = open_session(world_root, save_root, body.save_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    request.app.state.sessions[session.sid] = session
    return {"sid": session.sid, "world": session.world.meta.name}


@router.post("/sessions")
async def create_new_session(request: Request, body: CreateSessionBody):
    world_root = _world_root(request, body.world_id)
    save_root = _save_root(request, body.world_id)
    session = create_session(world_root, save_root, body.save_name)
    request.app.state.sessions[session.sid] = session
    return {"sid": session.sid, "world": session.world.meta.name}


@router.get("/sessions/{sid}")
async def get_session(request: Request, sid: str):
    session = _get_session(request, sid)
    return {
        "sid": session.sid,
        "world": session.world.meta.name,
        "clock": session.ledger.save.clock,
        "scene": session.scene_description(),
        "pending_count": len(session.candidates.list_pending()),
    }


@router.get("/sessions/{sid}/state")
async def get_state(request: Request, sid: str):
    session = _get_session(request, sid)
    player_scene = session.ledger.save.player_scene
    scene = next((s for s in session.world.scenes if s.id == player_scene), None)
    adjacent = []
    if scene is not None:
        for adj_id in scene.adjacent:
            adj_scene = next((s for s in session.world.scenes if s.id == adj_id), None)
            adjacent.append({"id": adj_id, "name": adj_scene.name if adj_scene else adj_id})
    present_ids = session.ledger.present_at(player_scene)
    present_names = [
        session.world.npcs.get(pid).name if pid in session.world.npcs else pid
        for pid in present_ids
    ]
    hooks = [h.model_dump() for h in session.ledger.save.hooks if h.status == "open"]
    return {
        "clock": session.ledger.save.clock,
        "scene": session.scene_description(),
        "scene_id": player_scene,
        "scene_name": scene.name if scene else player_scene,
        "adjacent": adjacent,
        "present": present_ids,
        "present_names": present_names,
        "hooks": hooks,
        "preset": request.app.state.global_preset.model_dump(),
        "pending": [
            {"candidate_id": c.candidate_id, "turn_id": c.turn_id, "mode": c.mode, "prose": c.prose}
            for c in session.candidates.list_pending()
        ],
    }


@router.get("/sessions/{sid}/ledger/events")
async def list_events(request: Request, sid: str):
    session = _get_session(request, sid)
    return {"events": session.ledger.narratives}


class PresetBody(BaseModel):
    style: str | None = None
    description_style: str | None = None
    banned_words: list[str] | None = None
    director_guidelines: str | None = None
    storyteller_preset: str | None = None


class PlayerBody(BaseModel):
    name: str | None = None
    appearance: str | None = None
    persona: str | None = None
    background: str | None = None


@router.put("/sessions/{sid}/presets")
async def update_presets(request: Request, sid: str, body: PresetBody):
    _get_session(request, sid)
    preset = request.app.state.global_preset
    if body.style is not None:
        preset.style = body.style
    if body.description_style is not None:
        preset.description_style = body.description_style
    if body.banned_words is not None:
        preset.banned_words = body.banned_words
    if body.director_guidelines is not None:
        preset.director_guidelines = body.director_guidelines
    if body.storyteller_preset is not None:
        preset.storyteller_preset = body.storyteller_preset
    save_global_preset(
        Path(request.app.state.settings.data_dir) / "presets.json",
        preset,
    )
    return {"ok": True}


@router.get("/presets")
async def get_global_preset(request: Request):
    return request.app.state.global_preset.model_dump()


@router.put("/presets")
async def update_global_preset(request: Request, body: PresetBody):
    preset = request.app.state.global_preset
    if body.style is not None:
        preset.style = body.style
    if body.description_style is not None:
        preset.description_style = body.description_style
    if body.banned_words is not None:
        preset.banned_words = body.banned_words
    if body.director_guidelines is not None:
        preset.director_guidelines = body.director_guidelines
    if body.storyteller_preset is not None:
        preset.storyteller_preset = body.storyteller_preset
    save_global_preset(
        Path(request.app.state.settings.data_dir) / "presets.json",
        preset,
    )
    return {"ok": True}


@router.get("/sessions/{sid}/player")
async def get_player(request: Request, sid: str):
    session = _get_session(request, sid)
    return session.ledger.save.player.model_dump()


@router.put("/sessions/{sid}/player")
async def update_player(request: Request, sid: str, body: PlayerBody):
    session = _get_session(request, sid)
    player = session.ledger.save.player
    if body.name is not None:
        player.name = body.name
    if body.appearance is not None:
        player.appearance = body.appearance
    if body.persona is not None:
        player.persona = body.persona
    if body.background is not None:
        player.background = body.background
    session.ledger.persist_save()
    return {"ok": True}


@router.get("/sessions/{sid}/world")
async def world_browser(request: Request, sid: str):
    session = _get_session(request, sid)
    world = session.world
    return {
        "overview": world.meta.model_dump(),
        "lorebook": [entry.model_dump() for entry in world.lorebook],
        "scenes": [scene.model_dump() for scene in world.scenes],
        "npcs": {npc_id: card.model_dump() for npc_id, card in world.npcs.items()},
        "axes": [axis.model_dump() for axis in world.axes],
        "events": session.ledger.narratives,
    }


@router.get("/sessions/{sid}/world/export")
async def export_world(request: Request, sid: str):
    session = _get_session(request, sid)
    world_root = _world_root(request, session.world.meta.id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(world_root.rglob("*")):
            if (
                path.is_file()
                and "saves" not in path.parts
                and path.name != "presets.json"
            ):
                zf.write(path, path.relative_to(world_root))
    buffer.seek(0)
    filename = f"{session.world.meta.id}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/sessions/{sid}/world/import")
async def import_world(request: Request, sid: str, body: ImportWorldBody):
    session = _get_session(request, sid)
    data = base64.b64decode(body.content)
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="not a zip file") from exc

    world_id = None
    try:
        raw_world = json.loads(zf.read("world.json"))
        world_id = raw_world.get("id")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="zip must contain world.json") from exc
    if not world_id:
        raise HTTPException(status_code=400, detail="world.json must contain id")

    content_root = Path(request.app.state.settings.content_root)
    dest = content_root / world_id
    if dest.exists():
        import datetime as _dt

        dest = content_root / f"{world_id}_{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}"

    dest.mkdir(parents=True, exist_ok=True)
    for member in zf.namelist():
        if member.startswith("saves/") or "/saves/" in member:
            continue
        if Path(member).name == "presets.json":
            continue
        target = (dest / member).resolve()
        if not target.is_relative_to(dest.resolve()):
            continue
        if member.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))

    return {"ok": True, "world_id": dest.name}


@router.post("/sessions/{sid}/reset")
async def reset_session(request: Request, sid: str):
    session = _get_session(request, sid)
    save = session.ledger.save
    save.clock = "2026-07-14T08:00:00"
    save.meta.next_event_id = 1
    save.player_scene = "main_street"
    save.narrative_preset = session.world.presets
    save.entities = {}
    save.axes = {}
    save.hooks = []
    save.pending_conflicts = []
    save.access_overrides = {}
    session.ledger.events = []
    session.ledger.narratives = []
    session.ledger.by_id = {}
    session.ledger.by_subject_loc = {}
    session.ledger.by_location = {}
    session.candidates.delete_all()
    session.ledger.persist_save()
    events_path = session.ledger.events_path
    if events_path.exists():
        events_path.unlink()
    return {"ok": True}


class SettingsBody(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    model_main: str | None = None
    model_cheap: str | None = None


@router.get("/settings")
async def get_system_settings(request: Request):
    s = request.app.state.settings
    return {
        "llm_base_url": s.llm_base_url,
        "llm_api_key": s.llm_api_key,
        "model_main": s.model_main,
        "model_cheap": s.model_cheap,
    }


@router.get("/settings/usage")
async def get_system_usage(request: Request):
    llm = request.app.state.llm
    if hasattr(llm, "get_usage"):
        return llm.get_usage()
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}


@router.put("/settings")
async def update_system_settings(request: Request, body: SettingsBody):
    s = request.app.state.settings
    if body.llm_base_url is not None:
        s.llm_base_url = body.llm_base_url
    if body.llm_api_key is not None:
        s.llm_api_key = body.llm_api_key
    if body.model_main is not None:
        s.model_main = body.model_main
    if body.model_cheap is not None:
        s.model_cheap = body.model_cheap

    from app.core.llm import LLMGateway

    request.app.state.llm = LLMGateway(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        max_concurrency=4,
        timeout=s.llm_timeout_seconds,
    )
    return {"ok": True}