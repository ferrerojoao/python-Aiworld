from __future__ import annotations

import base64
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.presets import save_global_preset
from app.core.store import write_json_atomic
from app.core.workorder import state_view
from app.runtime.session import create_session, open_session
from app.workers.drafter import run_world_draft
from app.world.draft import blank_world_assets, check_assets, validate_assets
from app.world.loader import check_world, save_world_assets
from app.world.models import NpcCard

router = APIRouter(prefix="/api")

# 世界 id = content/ 下的目录名，所以只收目录友好的字符（2026-09-13）。
_WORLD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class CreateSessionBody(BaseModel):
    world_id: str
    save_name: str = "main"  # 保留字段（进 save.json meta 与 sid），世界=存档后 UI 不再问存档名


class OpenSessionBody(BaseModel):
    world_id: str
    save_name: str = ""  # 兼容旧调用方；世界=存档 1:1 后不再区分存档名


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


class NewWorldBody(BaseModel):
    """快速造世界（2026-09-13）。

    ``payload`` 给了就按它落盘（L2 草稿的确认落盘），否则按 L1 种子字段造一个
    最小可玩包。两条路都先过 ``validate_assets`` 才写 content/。
    """

    world_id: str
    name: str = ""
    player_name: str = ""
    start_scene: str = ""
    opening: str = ""
    payload: dict | None = None


class WorldDraftBody(BaseModel):
    premise: str
    world_id: str = ""
    name: str = ""
    player_name: str = ""


def _world_root(request: Request, world_id: str) -> Path:
    root = Path(request.app.state.settings.content_root) / world_id
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"world not found: {world_id}")
    return root


def _get_session(request: Request, sid: str):
    session = request.app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    return session


def _read_world_name(root: Path) -> str:
    """世界名取 world.json 的 name（目录名只是 id）。读不出来就退回目录名。"""
    try:
        raw = json.loads((root / "world.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return root.name
    name = raw.get("name") if isinstance(raw, dict) else ""
    return name.strip() if isinstance(name, str) and name.strip() else root.name


@router.get("/worlds")
async def list_worlds(request: Request):
    """世界列表 = 存档列表（世界=存档 1:1）。每项带世界名与体检结论，
    有进行中存档的再带时钟（「昨天关机时的状态」）。"""
    content_root = Path(request.app.state.settings.content_root)
    worlds = []
    for path in sorted(content_root.glob("*")):
        if path.is_dir() and (path / "world.json").exists():
            problems = check_world(path)
            item = {
                "id": path.name,
                "name": _read_world_name(path),
                "ok": not problems,
                "problems": problems,
            }
            raw_save = _read_save_meta(path)
            if raw_save is not None:
                item["save_name"] = str(raw_save.get("save_name") or "")
                item["clock"] = str(raw_save.get("clock") or "")
            worlds.append(item)
    return {"worlds": worlds}


def _read_save_meta(world_root: Path) -> dict | None:
    try:
        raw = json.loads((world_root / "save.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


@router.get("/worlds/{world_id}/check")
async def check_world_assets(request: Request, world_id: str):
    """发布前的体检闸门：``check_world`` 此前只挂在 loader.py 的 CLI 上，
    API 与前端从不调用，于是保存是静默的（2026-09-13 暴露出来）。"""
    problems = check_world(_world_root(request, world_id))
    return {"ok": not problems, "problems": problems}


def _assets_from_body(body: NewWorldBody, world_id: str) -> dict:
    """草稿落盘路：以 payload 为准，顺手钉死 id/name（目录名说了算）。"""
    assets = dict(body.payload or {})
    overview = dict(assets.get("overview") or {})
    overview["id"] = world_id
    overview["name"] = (
        body.name.strip() or str(overview.get("name") or "").strip() or world_id
    )
    assets["overview"] = overview
    return assets


@router.post("/worlds/new")
async def create_world(request: Request, body: NewWorldBody):
    """L1 种子 / L2 草稿落盘：先体检、后落盘，坏包不进 content/。"""
    world_id = body.world_id.strip()
    if not _WORLD_ID_RE.match(world_id):
        raise HTTPException(
            status_code=400,
            detail="world_id 只能是英文/数字/下划线/连字符（它是 content/ 下的目录名）",
        )
    dest = Path(request.app.state.settings.content_root) / world_id
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"世界已存在：{world_id}")
    if body.payload:
        assets = _assets_from_body(body, world_id)
    else:
        player_name = body.player_name.strip()
        if not player_name:
            raise HTTPException(
                status_code=400, detail="主角名不能为空（事件日志按此名记录）"
            )
        assets = blank_world_assets(
            world_id, body.name, player_name, body.start_scene, body.opening
        )
    problems = validate_assets(assets)
    if problems:
        raise HTTPException(
            status_code=400, detail="未通过校验：" + "；".join(problems)
        )
    save_world_assets(dest, assets)
    return {"ok": True, "world_id": world_id, "problems": check_world(dest)}


@router.post("/worlds/draft")
async def draft_world(request: Request, body: WorldDraftBody):
    """L2 一句话草稿：LLM 出一整包，**先预览不落盘**。

    返回的 assets 就是 ``POST /worlds/new`` 的 payload 形状——玩家在工作台
    改完、点确认，才走落盘那条路。
    """
    premise = body.premise.strip()
    if not premise:
        raise HTTPException(status_code=400, detail="premise is required")
    settings = request.app.state.settings
    world_id = body.world_id.strip() or "draft"
    try:
        draft, assets, problems = await run_world_draft(
            request.app.state.llm,
            premise,
            world_id=world_id,
            name=body.name,
            player_name=body.player_name,
            model=settings.resolved_model("story"),
            temperature=settings.temp_writer,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"起草失败：{exc}") from exc
    return {"draft": draft.model_dump(), "assets": assets, "problems": problems}


@router.get("/worlds/{world_id}")
async def get_world(request: Request, world_id: str):
    """世界详情：has_save = 是否已有进行中的存档（世界=存档 1:1）。"""
    world_root = _world_root(request, world_id)
    raw_save = _read_save_meta(world_root)
    return {
        "id": world_id,
        "name": _read_world_name(world_root),
        "has_save": raw_save is not None,
        "save_name": str(raw_save.get("save_name") or "") if raw_save else "",
        "clock": str(raw_save.get("clock") or "") if raw_save else "",
    }


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
    # 旧语义（改资产 + 409 锁）已废弃：世界=存档实例，编辑一律走
    # PUT /sessions/{sid}/world 写实例。此处仅作兼容提示。
    raise HTTPException(status_code=410, detail="已改为编辑当前存档的世界实例：PUT /sessions/{sid}/world")


@router.put("/sessions/{sid}/world")
async def update_session_world(request: Request, sid: str, body: WorldAssetData):
    """就地编辑当前世界（单一真相源：写的就是 ``content/<world>/``，即时生效）。

    校验分两级（2026-09-13）：

    - **不变量级**（人物表恰好一张主角卡）先于落盘硬拦 400——存进去引擎就崩。
    - **语义问题**（``start_scene`` 越界 / 场景 id 重复 / 世界书条目没关键词）
      落盘后随响应带回 ``problems``，前端标黄警告——允许"中途拆结构"的半成品态
      存在，但它必须是**被看见的**，不能像以前那样静默通过。
    """
    session = _get_session(request, sid)
    payload = body.model_dump()
    payload["overview"]["id"] = session.world.meta.id
    try:
        problems = check_assets(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_world_assets(session.world_dir, payload)
    session.reload_world()
    return {"ok": True, "problems": problems}


# ---------------------------------------------------------------------------
# NPC 落卡（2026-09-19）：未落卡的确定人物 → 一张真正的人物卡
# ---------------------------------------------------------------------------

class NpcCardBody(BaseModel):
    """落卡表单：字段全可选，没写的就留空（这位角色的档案里暂时没有这一栏）。"""

    appearance: str = ""
    persona: str = ""
    private_note: str = ""
    personal_secrets: str = ""
    has_actor: bool = False


def _world_assets_payload(session) -> dict:
    """当前世界实例的资产形状（= ``PUT /sessions/{sid}/world`` 的 payload）。

    落卡改的就是这份资产，所以走**同一条写路径**——不为"补一张卡"另开一个
    写世界的口子（``draft.py`` 的明文纪律）。
    """
    world = session.world
    return {
        "overview": world.meta.model_dump(mode="json"),
        "lorebook": [e.model_dump(mode="json") for e in world.lorebook],
        "scenes": [s.model_dump(mode="json") for s in world.scenes],
        "npcs": {nid: c.model_dump(mode="json") for nid, c in world.npcs.items()},
        "axes": [a.model_dump(mode="json") for a in world.axes],
    }


def _unfiled_name(session, name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="需要一个名字")
    if name not in session.ledger.save.unfiled:
        raise HTTPException(status_code=404, detail=f"「{name}」不在未落卡名单里")
    return name


@router.get("/sessions/{sid}/unfiled/{name}/evidence")
async def unfiled_evidence(request: Request, sid: str, name: str):
    """落卡提案的**证据**：这位角色在已采纳正文里被写出过什么。

    只做**机械提取**（相关事件原文），一个字都不生成——"起草即提取，不编"。
    appearance / persona 那些栏位留给作者填：引擎替人编来历，正是本项目明确
    拒绝的那条路（编出来的内容会伪装成事实，从此再也分不清）。
    """
    session = _get_session(request, sid)
    name = _unfiled_name(session, name)
    ev = session.ledger.where_is(name) or {}
    events = [
        {
            "id": e["id"],
            "at": e.get("at", ""),
            "location": e.get("location") or "",
            "summary": e.get("summary", ""),
            "body": e.get("body", ""),
        }
        for e in session.ledger.by_participant.get(name, [])
    ][-8:]  # 最近 8 条够看清"正文已经写出什么"；更早的看事件日志
    return {
        "name": name,
        "location": ev.get("location") or "",
        "last_seen": ev.get("at") or "",
        "events": events,
    }


@router.post("/sessions/{sid}/unfiled/{name}/file")
async def file_unfiled_npc(request: Request, sid: str, name: str, body: NpcCardBody):
    """落卡：把"未落卡的确定人物"变成世界资产里的一张人物卡。

    闸门与写路径都复用资产编辑那一套（``check_assets`` + ``save_world_assets``）,
    所以落卡不新增第二条真相源。落成之后**键交还给卡**：名字从 ``unfiled`` 与
    ``featured_counts`` 里移除——他从此是有卡角色，不再需要计数。
    """
    session = _get_session(request, sid)
    name = _unfiled_name(session, name)
    if name in session.world.npcs:
        raise HTTPException(status_code=400, detail=f"人物表里已有「{name}」")
    payload = _world_assets_payload(session)
    payload["npcs"][name] = NpcCard(
        id=name,
        appearance=body.appearance,
        persona=body.persona,
        private_note=body.private_note or None,
        personal_secrets=body.personal_secrets or None,
        has_actor=body.has_actor,
    ).model_dump(mode="json")
    try:
        problems = check_assets(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_world_assets(session.world_dir, payload)
    session.reload_world()
    save = session.ledger.save
    if name in save.unfiled:
        save.unfiled.remove(name)
    save.featured_counts.pop(name, None)
    session.ledger.persist_save()
    return {"ok": True, "name": name, "problems": problems}


@router.post("/sessions/{sid}/unfiled/{name}/discard")
async def discard_unfiled_npc(request: Request, sid: str, name: str):
    """放弃落卡（未落卡名单里的 ×）：他退回去当即兴角色。

    ⚠️ **必须同时清零出场计数**——否则他下一轮一出场，计数仍 ≥2，立刻重新获键，
    撤销形同无效（与"同一个结束不能记两次"同源）。
    """
    session = _get_session(request, sid)
    name = _unfiled_name(session, name)
    save = session.ledger.save
    save.unfiled.remove(name)
    save.featured_counts.pop(name, None)
    session.ledger.persist_save()
    return {"ok": True, "name": name}


@router.get("/sessions/{sid}/world/check")
async def check_session_world(request: Request, sid: str):
    """体检当前世界。世界=存档 1:1 后与 ``GET /worlds/{id}/check`` 是同一份
    文件，保留两个入口只为前端语义顺口。"""
    session = _get_session(request, sid)
    problems = check_world(session.world_dir)
    return {"ok": not problems, "problems": problems}


@router.post("/sessions/{sid}/world/save-as")
async def save_session_world_as(request: Request, sid: str, body: SaveAsWorldBody):
    """复制当前世界（含全部演化）为一个新内容包——fork，不动原世界。"""
    _get_session(request, sid)  # 仅做存在性校验：未知 sid 一律 404
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
    try:
        save_world_assets(dest, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    try:
        session = open_session(_world_root(request, body.world_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    request.app.state.sessions[session.sid] = session
    return {"sid": session.sid, "world": session.world.meta.name}


@router.post("/sessions")
async def create_new_session(request: Request, body: CreateSessionBody):
    """在新世界里开一局（写 save.json 进世界目录）。已有存档 → 409，
    接着玩请走 ``POST /sessions/open``。"""
    world_root = _world_root(request, body.world_id)
    try:
        session = create_session(world_root, body.save_name)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"world already has a save: {body.world_id}（打开即可接着玩）",
        ) from exc
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
    player_scene = session.ledger.current_scene()
    # 最近去过的 3 个场景（从玩家事件流取，替代旧邻接导航）
    recent: list[str] = []
    for ev in reversed(session.ledger.narratives):
        loc = ev.get("location")
        if loc and loc not in recent:
            recent.append(loc)
        if len(recent) >= 3:
            break
    # 在场 = "还有谁在"（玩家视角）：主角就是镜头本人，不列进自己的视野
    # （2026-09-13 主角入人物表后 present_at 会带上他）。
    player_name = session.world.player_name()
    present_ids = [pid for pid in session.ledger.present_at(player_scene) if pid != player_name]
    # 目标是两级树（大目标=章节 / 小目标=节拍，2026-09-12）：状态栏需要
    # 「active 目标 + 挂在 active 大目标下的子目标（含已完成）」才能显示章节
    # 进度 x/y；其余历史目标（已闭合的主线、已完成/废弃的孤儿支线）不进状态栏。
    active_ids = {g.id for g in session.ledger.save.goals if g.status == "active"}
    goals = []
    for g in session.ledger.save.goals:
        if g.status != "active" and g.big_goal_id not in active_ids:
            continue
        item = g.model_dump()
        item["subject_name"] = g.subject or "玩家"
        goals.append(item)
    return {
        "clock": session.ledger.save.clock,
        # 最近一次时间结算留痕（P1）：顶栏时钟 hover 显示，用来解释"时钟为什么
        # 跳了这么久"——规则推了多少、审计估了多少、是否被保险丝截断、对钟是否
        # 被拒。纯诊断字段，前端缺它也不影响任何功能。
        "last_settlement": session.ledger.save.last_settlement,
        # 角色状态（REQ 〇章；Step 2）：当前清单 + 最近一次采纳的变更留痕。
        # 前端展示与撤销入口是后续（眼下先让后端说得清"编剧为什么认为他免疫刀剑"）。
        "states": {
            name: [it.model_dump() for it in runtime.states]
            for name, runtime in session.ledger.save.entities.items()
            if runtime.states
        },
        # 状态面板的数据（Step 2b）：**由引擎侧生成**（复用提示词那套 cap 与截断），
        # 所以面板上"编剧能看到哪几条"与提示词字面同源——前端不要自己算 cap。
        "state_view": state_view(session.ledger, present_ids),
        # 面板要单列主角（提示词的「玩家资料」段恒在），而 present_ids 里没有他。
        "player_name": player_name,
        "last_state_change": session.ledger.save.last_state_change,
        "scene": session.scene_description(),
        "scene_id": player_scene,
        "recent_scenes": recent,
        "present": present_ids,
        "present_names": present_ids,
        # 未落卡的确定人物（NPC 落卡机制，2026-09-19）：有键无卡——引擎已经
        # 认他是"人"（有名字 + 与玩家有往来 ≥2 轮），但世界资产里还没有他的
        # 人物档案。`present` 判定沿用同一份 present_ids，所以左栏可以直接用
        # 它对在场者打「未落卡」标记；`location` 支撑"我离开酒馆后他还挂在哪"
        # 这个全局入口（玩家不点落卡就继续走，他的名字不能就此消失）。
        "unfiled": [
            {
                "name": name,
                "location": (session.ledger.where_is(name) or {}).get("location") or "",
                "present": name in present_ids,
            }
            for name in session.ledger.save.unfiled
        ],
        "goals": goals,
        "preset": request.app.state.global_preset.model_dump(),
        "pending": [
            {"candidate_id": c.candidate_id, "turn_id": c.turn_id, "mode": c.mode, "prose": c.prose}
            for c in session.candidates.list_pending()
        ],
    }


@router.get("/sessions/{sid}/ledger/events")
async def list_events(request: Request, sid: str):
    session = _get_session(request, sid)
    # access_view() 而非 narratives：改判结果必须在这里看得见。直接给原始事件
    # 会让"改判私密"在界面上永远是【公开】（2026-09-18 修的 bug）。
    return {"events": session.ledger.access_view()}


class PresetBody(BaseModel):
    writer_guidelines: str | None = None
    banned_words: list[str] | None = None
    style_sample: str | None = None


def _apply_preset_update(preset, body: PresetBody) -> None:
    if body.writer_guidelines is not None:
        preset.writer_guidelines = body.writer_guidelines
    if body.banned_words is not None:
        preset.banned_words = body.banned_words
    if body.style_sample is not None:
        preset.style_sample = body.style_sample


class PlayerBody(BaseModel):
    name: str | None = None
    appearance: str | None = None
    persona: str | None = None
    private_note: str | None = None
    personal_secrets: str | None = None


@router.put("/sessions/{sid}/presets")
async def update_presets(request: Request, sid: str, body: PresetBody):
    _get_session(request, sid)
    preset = request.app.state.global_preset
    _apply_preset_update(preset, body)
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
    _apply_preset_update(preset, body)
    save_global_preset(
        Path(request.app.state.settings.data_dir) / "presets.json",
        preset,
    )
    return {"ok": True}


@router.get("/sessions/{sid}/player")
async def get_player(request: Request, sid: str):
    """主角资料：就是人物表里 is_player 的那张卡。

    对外沿用 ``name`` 字段（前端表单与旧接口同形）：卡的 ``id`` 即名字。
    """
    session = _get_session(request, sid)
    card = session.world.player()
    if card is None:
        raise HTTPException(status_code=404, detail="world has no player card")
    return {**card.model_dump(), "name": card.id}


def _write_player_card(session, card) -> None:
    """主角卡落盘（人物表 + 实例文件），并把新的键接回内存世界。"""
    world = session.world
    write_json_atomic(
        session.world_dir / "npcs" / f"{card.id}.json",
        card.model_dump(),
    )
    world.npcs[card.id] = card


@router.put("/sessions/{sid}/player")
async def update_player(request: Request, sid: str, body: PlayerBody):
    """编辑主角：主角就是人物表里 is_player 的那张卡（2026-09-13）。

    改名 = 换人物表键 = 换事件日志此后记录的名字；旧日志保持原有名字不改写
    （用户口径：日志直接记人名，中途换主角不影响日志）。
    """
    session = _get_session(request, sid)
    card = session.world.player()
    if card is None:
        raise HTTPException(status_code=404, detail="world has no player card")
    old_id = card.id
    new_id = (body.name or "").strip()
    if new_id and new_id != old_id:
        if new_id in session.world.npcs:
            raise HTTPException(status_code=400, detail=f"人物表里已有「{new_id}」")
        card.id = new_id
    for field in ("appearance", "persona", "private_note", "personal_secrets"):
        value = getattr(body, field)
        if value is not None:
            setattr(card, field, value)
    if new_id and new_id != old_id:
        del session.world.npcs[old_id]
        old_file = session.world_dir / "npcs" / f"{old_id}.json"
        if old_file.exists():
            old_file.unlink()
        # 目标归属用空串哨兵表示主角，无需跟着改名；镜头位置由存档小抄保管。
    _write_player_card(session, card)
    session.ledger.persist_save()
    return {"ok": True, "name": card.id}


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
        # 事件日志标签页读的就是这里——同样要走 access_view()，否则改判看不见。
        "events": session.ledger.access_view(),
    }


@router.get("/sessions/{sid}/world/export")
async def export_world(request: Request, sid: str):
    """导出资产包 = 世界资产的快照（不含运行态）。用户存到自己电脑上，
    想回到初始状态时导入它就会得到一个新世界。"""
    session = _get_session(request, sid)
    world_root = session.world_dir
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(world_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(world_root)
            parts = rel.parts
            if not parts or parts[0] == "candidates":
                continue
            if path.name in ("save.json", "events.jsonl", "presets.json"):
                continue
            zf.write(path, rel.as_posix())
    buffer.seek(0)
    filename = f"{session.world.meta.id}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/sessions/{sid}/export")
async def export_save(request: Request, sid: str):
    """全量备份：世界资产 + save.json + events.jsonl（候选除外）。

    与「导出资产包」不同：这里连事件日志（世界全部历史）一起打包，用于
    备份/换机/复盘。导入落点 = ``content/<world_id>/`` 本身（世界=存档）。
    """
    session = _get_session(request, sid)
    save_dir = session.save_dir
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(save_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(save_dir)
            # 候选是临时态（未采纳的提案），不入备份。
            if rel.parts and rel.parts[0] == "candidates":
                continue
            zf.write(path, rel.as_posix())
    buffer.seek(0)
    name = f"{session.world.meta.id}_{session.ledger.save.meta.save_name}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


@router.post("/saves/import")
async def import_save(request: Request, body: ImportWorldBody):
    """Import a full-backup zip produced by GET /sessions/{sid}/export.

    落点 = ``content/<world_id>/`` 本身（世界=存档，单一真相源）。兼容两种
    包内布局：新备份资产在根层；旧备份资产在 ``world/`` 前缀下（剥前缀）。
    目标世界已有进行中的存档 → 409 拒绝（绝不覆盖正在玩的世界）；想恢复
    先删掉坏掉的世界再导入，或改个 id 导入为新世界。
    """
    data = base64.b64decode(body.content)
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="not a zip file") from exc

    names = zf.namelist()
    if "save.json" not in names:
        raise HTTPException(status_code=400, detail="zip must contain save.json (导出存档包)")
    try:
        raw_save = json.loads(zf.read("save.json"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad save.json: {exc}") from exc
    meta = raw_save.get("meta") or {}
    world_id = str(meta.get("world_id") or "").strip()
    save_name = str(meta.get("save_name") or "").strip()
    if not world_id or not save_name:
        raise HTTPException(status_code=400, detail="save.json meta must contain world_id and save_name")

    content_root = Path(request.app.state.settings.content_root)
    world_root = content_root / world_id
    if (world_root / "save.json").exists():
        raise HTTPException(
            status_code=409,
            detail=f"world already has a save: {world_id}（不覆盖正在玩的世界；先删除它或换个 id 导入为新世界）",
        )
    world_root.mkdir(parents=True, exist_ok=True)

    for member in names:
        if member.endswith("/"):
            continue
        if Path(member).name == "presets.json":
            continue
        rel = member
        if rel.startswith("world/"):  # 旧备份布局：资产带 world/ 前缀
            rel = rel[len("world/"):]
        if not rel or rel.startswith("candidates/") or "/candidates/" in rel:
            continue
        target = (world_root / rel).resolve()
        if not target.is_relative_to(world_root.resolve()):
            continue  # 防 zip 路径穿越
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(member))

    write_json_atomic(world_root / "save.json", raw_save)

    session = open_session(world_root)
    request.app.state.sessions[session.sid] = session
    return {
        "ok": True,
        "sid": session.sid,
        "world_id": world_id,
        "save_name": save_name,
    }


@router.post("/sessions/{sid}/world/import")
async def import_world(request: Request, sid: str, body: ImportWorldBody):
    _get_session(request, sid)  # 仅做存在性校验：未知 sid 一律 404
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
    except Exception as exc:
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


@router.post("/sessions/{sid}/states/{state_id}/revoke")
async def revoke_state(request: Request, sid: str, state_id: str):
    """撤销一条角色状态（Step 2b，2026-09-14）：删条目 + 落一条对冲记账事件。

    玩家动作、不经审计；正文与事件日志一律不动（史实不可变，改的是"编剧此后
    认为他是什么"这个运行态）。找不到 → 404（多半是已经撤过 / 已到期 / 刚重置）。
    """
    session = _get_session(request, sid)
    runner = request.app.state.turn_runner_factory(session)
    if not runner.transaction.revoke_state(state_id):
        raise HTTPException(status_code=404, detail="状态不存在（可能已被撤销）")
    return {"ok": True}


@router.post("/sessions/{sid}/reset")
async def reset_session(request: Request, sid: str):
    from app.runtime.session import world_start_time, write_opening_event

    session = _get_session(request, sid)
    # 世界实例原地保留（工作台对概览/NPC 卡/场景/世界书的编辑不丢），
    # 只清运行态：事件、候选、实体演化、目标、知情覆盖等，开场重建。
    save = session.ledger.save
    save.clock = world_start_time(session.world)
    save.meta.next_event_id = 1
    save.player_scene = session.world.start_scene_id()
    save.narrative_preset = session.world.presets
    save.entities = {}
    save.axes = {}
    save.goals = []
    save.access_overrides = {}
    save.audit_last_error = None
    save.active_lore_ids = []
    # 结算留痕是诊断字段，但重置后它指的是**重置前**那一次的结算（时钟、依据档位
    # 全对不上）——留着只会误导顶栏时钟的 hover，一并清掉（2026-09-14）。
    save.last_settlement = None
    save.last_state_change = None
    # 未落卡者与其出场计数是**本局的运行态**（键），随世界重置清零——
    # 人物卡是资产、留在工作台不动（2026-09-19）。
    save.featured_counts = {}
    save.unfiled = []
    session.ledger.events = []
    session.ledger.narratives = []
    session.ledger.by_id = {}
    session.ledger.by_location = {}
    session.ledger._rebuild_indexes()  # 清空并重建派生索引（last_location/by_participant）
    session.candidates.delete_all()
    events_path = session.ledger.events_path
    if events_path.exists():
        events_path.unlink()
    # The world's opening prose starts every fresh ledger, including resets.
    if not write_opening_event(session):
        session.ledger.persist_save()
    return {"ok": True}


class SettingsBody(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    model_main: str | None = None
    model_cheap: str | None = None
    reasoning_effort: str | None = None
    qc_enabled: bool | None = None
    # 注入上限（2026-09-16）：0 = 给全部；event_log_full 最少 1（见 Settings.limits）。
    event_log_limit: int | None = None
    event_log_full: int | None = None
    known_set_limit: int | None = None


@router.get("/settings")
async def get_system_settings(request: Request):
    s = request.app.state.settings
    return {
        "llm_base_url": s.llm_base_url,
        "llm_api_key": s.llm_api_key,
        "model_main": s.model_main,
        "model_cheap": s.model_cheap,
        "reasoning_effort": s.reasoning_effort or "auto",
        "qc_enabled": s.qc_enabled,
        "event_log_limit": s.event_log_limit,
        "event_log_full": s.event_log_full,
        "known_set_limit": s.known_set_limit,
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
    if body.reasoning_effort is not None:
        effort = body.reasoning_effort.lower()
        s.reasoning_effort = "" if effort in {"auto", "none", ""} else effort
    if body.qc_enabled is not None:
        s.qc_enabled = body.qc_enabled
    # 注入上限：clamp 交给 Settings.limits()（负数→0，正文条数→至少 1），
    # 这里只做落值，避免"接口层夹一次、装配层再夹一次"的两套口径。
    if body.event_log_limit is not None:
        s.event_log_limit = body.event_log_limit
    if body.event_log_full is not None:
        s.event_log_full = body.event_log_full
    if body.known_set_limit is not None:
        s.known_set_limit = body.known_set_limit
    from app.config import save_settings_overrides
    from app.core.llm import LLMGateway

    request.app.state.llm = LLMGateway(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        max_concurrency=4,
        timeout=s.llm_timeout_seconds,
        reasoning_effort=s.reasoning_effort,
    )
    # UI 改的设置落盘（data/settings.json），重启后由 lifespan 覆盖回来。
    save_settings_overrides(s.data_dir / "settings.json", s)
    return {"ok": True}