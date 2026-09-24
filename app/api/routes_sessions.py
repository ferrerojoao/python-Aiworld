from __future__ import annotations

import base64
import io
import json
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.presets import save_global_preset
from app.core.store import write_json_atomic
from app.core.workorder import state_view
from app.runtime.session import create_session, open_session
from app.runtime.trace import TraceRecorder
from app.workers.drafter import run_world_draft
from app.workers.landing import draft_npc_card, draft_scene
from app.world.draft import blank_world_assets, check_assets, validate_assets
from app.world.images import (
    EXT_TO_MEDIA,
    MAX_UPLOAD_BYTES,
    ImageRejected,
    prune_orphan_assets,
    resolve_asset,
    write_asset,
)
from app.world.loader import check_world, save_world_assets
from app.world.models import PLAYER_PLACEHOLDER, SCENE_PLACEHOLDER, NpcCard, Scene

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
            # 模型名与出口由 run_world_draft 的默认角色（story）决定，见 LLMRouter。
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


def _player_keys(npcs: dict) -> set[str]:
    """人物表里"主角"的**键**集合（键 = 卡自己的 ``id``）。

    ⚠️ 取 ``card.id`` 而不是外层 dict 的键：``save_world_assets`` 按 dict 键命名
    文件，``load_world`` 却按 ``card.id`` 建键——两者不一致时以 ``card.id`` 为准，
    因为**那才是落盘并重载之后真正生效的键**（前端 ``readNpcs`` 本来就保证两者
    一致，所以只有手改请求体才会碰见这种错位）。
    ``WorldContent.player_name()`` 用的也是 ``card.id``，判据必须与它同源。

    同时吃两种形状：内存里的 ``NpcCard`` 模型与请求体里的裸 dict——**判据只有
    一份**，形状差异在这里消化掉，别在调用处各写一遍。
    """
    keys: set[str] = set()
    for npc_id, card in (npcs or {}).items():
        if isinstance(card, dict):
            marked, name = card.get("is_player"), card.get("id")
        else:
            marked, name = getattr(card, "is_player", False), getattr(card, "id", None)
        if marked:
            keys.add(str(name or npc_id))
    return keys


def _assert_player_unlocked(session, incoming_npcs: dict) -> None:
    """主角锁定（2026-09-21 拍板）：本局一开演，"谁是主角"就冻结。

    判据是**集合比对**而非逐 case 检字段——换人（把 is_player 勾到另一张卡）、
    改名（改主角卡的 ``id``，而日志键就是人名）、删人（连主角卡一起删）三种表现
    由同一条收口。**别再按 case 打补丁**。

    顺序在 ``check_assets`` **之前**：先过不变量校验会抛"人物表必须有且只有一张
    主角卡——当前 0 张"这种技术话，而玩家需要知道的是"已开始游戏，主角已锁定"。
    状态码用 **409** 而非 400：不是"你填错了"，是"这个存档状态不允许"
    （与 ``PUT /worlds/{id}`` 那条 410 的语义分区一致）。
    """
    if not session.ledger.game_started():
        return
    before = _player_keys(session.world.npcs)
    after = _player_keys(incoming_npcs)
    if before == after:
        return
    current = "、".join(sorted(before)) or "无"
    raise HTTPException(
        status_code=409,
        detail=(
            f"本局已开始，主角已锁定（当前主角：{current}）。"
            "事件日志一旦落了正文，主角就不能再换（改名、删卡同理）——"
            "日志里的旧名不会迁移，历史目标与状态也全都挂在旧名上。"
            "要换主角请先「重置世界」，或用「另存为」开一个新世界再改。"
        ),
    )


def _player_lock_problems(session) -> list[str]:
    """已开局的档丢了主角卡 → **只报不改**（2026-09-21 拍板 4）。

    ``load_world`` 对"人物表里没有 is_player 卡"会**无条件**补一张占位主角卡
    （引擎处处以"主角名"为键，缺卡会静默失配）——所以不补是不行的，但已开局的档
    上被补一张占位卡，实际效果就是**一次隐式换人**。这里不做还原、不猜原主、
    不自动修：只把这件事说出来。玩家唯一能走的路是「重置世界」。
    """
    if not session.ledger.game_started():
        return []
    player = session.world.player()
    if player is None or player.id != PLAYER_PLACEHOLDER:
        return []
    return [
        "本局已开始，但磁盘上找不到主角卡——载入时已补占位卡「主角」。"
        "这等于换了一次主角：主角已锁定，只能先「重置世界」再改名；"
        "此前事件日志里记的旧主角名不会迁移。"
    ]


@router.put("/sessions/{sid}/world")
async def update_session_world(request: Request, sid: str, body: WorldAssetData):
    """就地编辑当前世界（单一真相源：写的就是 ``content/<world>/``，即时生效）。

    校验分两级（2026-09-13）：

    - **不变量级**（人物表恰好一张主角卡）先于落盘硬拦 400——存进去引擎就崩。
    - **语义问题**（``start_scene`` 越界 / 场景 id 重复 / 世界书条目没关键词）
      落盘后随响应带回 ``problems``，前端标黄警告——允许"中途拆结构"的半成品态
      存在，但它必须是**被看见的**，不能像以前那样静默通过。

    主角锁定（2026-09-21）插在两级之前：已开局时"谁是主角"冻结 → 409。
    """
    session = _get_session(request, sid)
    payload = body.model_dump()
    payload["overview"]["id"] = session.world.meta.id
    _assert_player_unlocked(session, payload.get("npcs") or {})
    try:
        problems = check_assets(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_world_assets(session.world_dir, payload)
    # 附图孤儿清理（2026-09-24）：上传只写文件、字段靠这次保存落盘，所以"换图 /
    # 移除 / 传了但放弃改动"都会留下没人引用的图，在这里统一带走。
    # ⚠️ 只有本端点能调用它——payload 是**完整**资产形状；半份资产会误删。
    prune_orphan_assets(session.world_dir, payload)
    session.reload_world()
    return {"ok": True, "problems": problems}


# ---------------------------------------------------------------------------
# 附图（2026-09-24）：场景图 / 人物图的上传与取用
#
# 规格（尺寸 / 比例 / 体积）与推导见 docs/方案-场景与人物附图-AIWorld.md §4.5，
# 归一化口径见 §4.4，代码在 app/world/images.py。
# ---------------------------------------------------------------------------


@router.post("/sessions/{sid}/assets")
async def upload_asset(request: Request, sid: str, kind: str, subject: str = ""):
    """上传一张附图。**收原始字节，不是 multipart。**

    ⚠️ 收原始字节是因为 ``python-multipart`` 没装（用 ``UploadFile`` 会直接报
    ``Form data requires "python-multipart" to be installed.``）。这里零新依赖：
    客户端 ``fetch(url, {method: "POST", body: file})`` 即可；``kind`` / ``subject``
    走查询参数，``Content-Type`` 只当声明（真类型由 magic bytes + Pillow 解码判定）。

    🔴 **本端点只写文件，不改世界资产。** 字段（``Scene.image`` / ``NpcCard.portrait``）
    由工作台的「保存」一起落盘——"保存才落盘"是工作台既有的事务边界，不为图片破它。
    因此换图 / 移除 / 传了不保存都会留下没人引用的文件，由保存那一刻的孤儿清理带走
    （见 ``PUT /sessions/{sid}/world`` 与 ``prune_orphan_assets``）。

    ⚠️ **不做主体名白名单**（与方案初稿不同）：工作台里"新加的人物 / 场景"在保存前
    还不存在于资产里，白名单会把这一路直接堵死。改名与目录穿越的风险由"**文件名完全
    由服务端生成**"消掉——主体名先安全化，再拼一段内容哈希，客户端给不出路径。
    """
    session = _get_session(request, sid)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片太大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB）",
        )
    raw = await request.body()
    try:
        rel = write_asset(session.world_dir, kind, subject, raw)
    except ImageRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"ok": True, "path": rel, "kind": kind}


@router.get("/sessions/{sid}/assets/{kind}/{name}")
async def get_asset(request: Request, sid: str, kind: str, name: str):
    """取一张附图的字节。

    ⚠️ **必须在 ``/api`` 下**：静态挂载 ``app.mount("/", StaticFiles(web/dist))`` 在
    ``include_router`` **之后**注册，所以只有 ``/api/*`` 受令牌保护（``security.py``
    的 ``PROTECTED_PREFIX``）。挪到 ``/api`` 之外 = 图片在局域网里裸奔，
    而文件名还含人名 / 场景名。

    ⚠️ 前端**不能用 ``<img src>`` 直连**：``<img>`` 带不了 ``Authorization`` 头，
    非本机访问必然 401，而底座是"缺图不显示"——于是图会**静默消失**，本机测永远正常。
    前端走带令牌的 fetch → blob（见 ``web/dist/app.js`` 的 ``imageUrl``）。

    ``immutable`` 能这么用，靠的是**内容寻址命名**：同一 URL 永远对应同一张图。
    """
    session = _get_session(request, sid)
    path = resolve_asset(session.world_dir, kind, name)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这张图")
    media = EXT_TO_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


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
    from app.workers.landing import evidence_of

    return {
        "name": name,
        "location": ev.get("location") or "",
        "last_seen": ev.get("at") or "",
        "events": evidence_of(session.ledger.by_participant.get(name, [])),
    }


@router.get("/sessions/{sid}/unfiled/{name}/draft")
async def unfiled_npc_draft(request: Request, sid: str, name: str):
    """落卡**草稿**：LLM 从已采纳正文里总结出 外貌 / 人格。

    ⚠️ 这是**草稿不是事实**（2026-09-19 教义变更）：旧口径是"只机械提取、拟稿由
    玩家完成"，现在允许 LLM 起草——防线换成"输入只有他自己的原文证据 + 提示词
    禁止超出正文 + 前端标成「AI 草稿」并与原文并排"。落盘与否仍由玩家拍板
    （``POST .../file``），本端点**不写任何状态**。
    """
    session = _get_session(request, sid)
    name = _unfiled_name(session, name)
    trace = TraceRecorder(request.app.state.llm)
    session.debug_trace = trace.entries
    # 落卡草稿是**辅助活**：worker="landing" 属 AUX_WORKERS → 辅助模型 + 辅助出口。
    draft = await draft_npc_card(trace, session.ledger, name, worker="landing")
    return {"name": name, **draft}


# ---------------------------------------------------------------------------
# 落卡窗口 2.0（2026-09-19）：新场景进同一个窗口
#
# 场景此前是**审计单方面静默落卡**的：``register_scene=true`` 就直接写
# ``scenes.json``，而描述只能是占位句（"暂无描述。"）——那句还每轮都被注入。
# 资产层凭空多一个没描述的场景、且没人被通知去补。现在审计只记候选册
# （``save.locations``），写资产的唯一入口在这里，与人物侧同一条纪律：
# **引擎只给候选，玩家拍板**。
# ---------------------------------------------------------------------------


class SceneCardBody(BaseModel):
    """场景落卡表单：只收「描述」（可感知区，进 ``Scene.perceivable``）。

    **名称不作为入参**——它是场景键，也是所有历史事件的 ``location``；改名会让
    它们全部落空（退化成一次性布景）。别名从候选册自动带上，不在这里改。
    """

    perceivable: str = ""


def _pending_scene(session, name: str) -> tuple[str, object]:
    """取一个待落卡地点（不存在 / 已处理过 → 404）。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="需要一个地点名")
    item = session.ledger.save.locations.get(name)
    if item is None:
        raise HTTPException(status_code=404, detail=f"「{name}」不在待落卡名单里")
    return name, item


@router.get("/sessions/{sid}/locations/{name}/evidence")
async def scene_evidence(request: Request, sid: str, name: str):
    """待落卡地点的证据：已采纳正文里发生在这里的原文（只机械提取，不生成）。"""
    from app.workers.landing import evidence_of

    session = _get_session(request, sid)
    name, item = _pending_scene(session, name)
    events = evidence_of(session.ledger.by_location.get(name, []))
    return {
        "name": name,
        "aliases": list(item.aliases),
        "last_seen": events[-1]["at"] if events else "",
        "events": events,
    }


@router.get("/sessions/{sid}/locations/{name}/draft")
async def scene_landing_draft(request: Request, sid: str, name: str):
    """场景落卡**草稿**：LLM 总结出「一眼能感知到」的描述。只返回，不落盘。"""
    session = _get_session(request, sid)
    name, _item = _pending_scene(session, name)
    trace = TraceRecorder(request.app.state.llm)
    session.debug_trace = trace.entries
    draft = await draft_scene(trace, session.ledger, name, worker="landing")
    return {"name": name, **draft}


@router.post("/sessions/{sid}/locations/{name}/file")
async def file_landing_scene(request: Request, sid: str, name: str, body: SceneCardBody):
    """场景落卡：把待落卡地点变成场景表里的正式场景。

    走**与资产编辑同一条写路径**（``check_assets`` + ``save_world_assets``），
    不新增第二条真相源。描述留空 → 用占位句 ``SCENE_PLACEHOLDER``；与旧的自动
    注册行为一致，区别是这次是**玩家看着空栏点的确定**，不是引擎偷偷写的。

    别名（候选册里攒的变体名）一并写进 ``aliases``：同一个地点换个叫法，下一轮
    才认得出来（否则"巷子深处的旧楼"会被当成新地点再排一条）。

    ⚠️ ``region`` 一律留空 = 全域公共区：**落卡之后这里的公开事件会进所有人的
    风闻范围**（落卡前是 adhoc 哨兵 = 谁都不风闻），而且**追溯生效**（
    ``_event_region`` 查表现算）。这是落卡的实质后果，前端必须写出来。
    """
    session = _get_session(request, sid)
    name, item = _pending_scene(session, name)
    if any(s.id == name for s in session.world.scenes):
        raise HTTPException(status_code=400, detail=f"场景表里已有「{name}」")
    payload = _world_assets_payload(session)
    payload["scenes"] = [
        *payload["scenes"],
        Scene(
            id=name,
            aliases=[name, *item.aliases],
            perceivable=(body.perceivable or "").strip() or SCENE_PLACEHOLDER,
        ).model_dump(mode="json"),
    ]
    try:
        problems = check_assets(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_world_assets(session.world_dir, payload)
    session.reload_world()
    session.ledger.save.locations.pop(name, None)
    session.ledger.persist_save()
    return {"ok": True, "name": name, "problems": problems}


@router.post("/sessions/{sid}/locations/{name}/discard")
async def discard_landing_scene(request: Request, sid: str, name: str):
    """× 定为临时：这个地点不再自动入队。

    ⚠️ 与人物侧的 × **不对称，而且必须如此**：人物的键靠"累计 2 轮往来"重新挣
    回来（那是真实信号，不是唠叨）；场景的 ``register_scene`` 是个 boolean，
    **每访问一次就重发一次**——不静音的话玩家每次回到那个地方都要被问一遍
    （M14 类失败：入口惹人烦 → 玩家绕开）。别名仍留在册里，解析域照旧认得它。

    要改成正式场景：世界工作台的场景表是另一条路。
    """
    session = _get_session(request, sid)
    name, item = _pending_scene(session, name)
    item.status = "dismissed"
    session.ledger.persist_save()
    return {"ok": True, "name": name}


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
    # 已开局却丢了主角卡（外部手改磁盘）：只报不改（见 _player_lock_problems）。
    problems.extend(_player_lock_problems(session))
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
    # ⚠️ 这个名单**不能**放进主角：提示词的在场名单与状态面板 `state_view` 都按
    # "主角另有 [主角] 段"来用（`STATE_CAP_PLAYER` ≠ `STATE_CAP_NPC`），混进来会
    # 重复计数、左栏的「未落卡」标记也会跟着错。左栏要的"场景里有谁"另用一个独立
    # 布尔 `player_present` 表达（2026-09-21 用户要求把主角显示在左栏在场名单里）。
    player_name = session.world.player_name()
    in_scene = session.ledger.present_at(player_scene)
    present_ids = [pid for pid in in_scene if pid != player_name]
    # 主角在不在镜头场景里。**判据仍是引擎的位置推导**，前端不另算：提交时
    # `location` 与 `save.player_scene` 同源写、主角恒进 `participants`（transaction
    # 里兜底），所以落过账之后恒为 True；只有"连一条带位置的流水都没有"（世界没写
    # 开场白）才为 False——那时引擎确实没有证据说他站在这里。
    player_present = player_name in in_scene
    # 场景卡（2026-09-24）：一张卡回答"此刻这一幕：哪里 + 谁在"。
    # 图**永不进提示词**——这里只是把引擎已有的东西也给玩家看。
    # ⚠️ 取的是 `perceivable` 原文，不是 `session.scene_description()`：后者已经
    # 把「在场：…」拼进去了，卡片另有在场一格，用它会重复。
    scenes_by_id = {s.id: s for s in session.world.scenes}
    scene = scenes_by_id.get(player_scene)
    scene_image = scene.image if scene is not None else ""
    scene_perceivable = scene.perceivable if scene is not None else ""

    def _portrait_of(nid: str) -> str:
        """肖像路径；没有卡（未落卡人物）自然没有图。"""
        card = session.world.npcs.get(nid)
        return card.portrait if card is not None else ""

    # 「在场」一格一人：**主角排第一 + 挂「主角」标**（与左栏一致）。
    # ⚠️ 这是 **UI 层**的一致，和"场景底稿正文剔主角"（提示词层）不是一回事，别合并。
    # ⚠️ 一个人**有图没图都占一格**（没图就只显示名字）：否则一上头像，"谁有图谁没图"
    # 会让人数忽多忽少，看着像有人凭空消失。缺图只吞图，不吞信息。
    present_view: list[dict] = []
    if player_present:
        present_view.append(
            {"name": player_name, "portrait": _portrait_of(player_name), "is_player": True}
        )
    present_view += [
        {"name": pid, "portrait": _portrait_of(pid), "is_player": False} for pid in present_ids
    ]
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
        # 场景卡（2026-09-24）：场景图相对路径 + `perceivable` 原文 + 「在场」一格一人。
        # 图是**纯 UI**：不进注入、不参与任何判定（方案 §2 底座 1/3）。
        "scene_image": scene_image,
        "scene_perceivable": scene_perceivable,
        "present_view": present_view,
        # 镜头场景里有主角吗（2026-09-21）：左栏「在场人物」要把他列出来并打「主角」
        # 标，而 `present_ids` 是"还有谁在"（不含他）。见上面的判据说明。
        "player_present": player_present,
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
        # 待落卡的**地点**（落卡窗口 2.0，2026-09-19）：审计建议落卡但玩家还没
        # 拍板的地方。dismissed（玩家定为临时）不进这里——它们不会再打扰玩家。
        # 与人物侧的区别只在 × 的语义（见 `discard_landing_scene`）。
        "unfiled_scenes": [
            {"name": name, "aliases": list(item.aliases)}
            for name, item in session.ledger.save.locations.items()
            if item.status == "pending"
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


@router.get("/sessions/{sid}/world")
async def world_browser(request: Request, sid: str):
    session = _get_session(request, sid)
    world = session.world
    return {
        "overview": world.meta.model_dump(),
        "lorebook": [entry.model_dump() for entry in world.lorebook],
        "scenes": [scene.model_dump() for scene in world.scenes],
        "npcs": {npc_id: card.model_dump() for npc_id, card in world.npcs.items()},
        # 事件日志标签页读的就是这里——同样要走 access_view()，否则改判看不见。
        "events": session.ledger.access_view(),
        # 主角锁定（2026-09-21）：判据在账本（Ledger.game_started），前端只消费结论。
        # 前端拿它把 is_player 勾选框与主角卡的 id 输入框置灰——**只是礼貌**，
        # 真闸门是 PUT /world 的 409（M14 教训：只拦 UI 等于没拦）。
        "player_locked": session.ledger.game_started(),
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
    # 地点候选册同理：审计的建议是本局的运行态，随世界重置清零；场景是资产、
    # 留在工作台不动（落卡窗口 2.0，2026-09-19）。
    save.locations = {}
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
    # 辅助模型的独立出口（2026-09-20）：留空 = 跟随主接口那一套。
    cheap_base_url: str | None = None
    cheap_api_key: str | None = None
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
        "cheap_base_url": s.cheap_base_url,
        "cheap_api_key": s.cheap_api_key,
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
    # 辅助 Base URL **必须 strip**：空串是"跟随主接口"的开关值，界面里一个
    # 多余的空格若被原样存下，就会变成"配了个叫 空格 的出口"，报错还很难认。
    if body.cheap_base_url is not None:
        s.cheap_base_url = body.cheap_base_url.strip()
    if body.cheap_api_key is not None:
        s.cheap_api_key = body.cheap_api_key
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
    from app.core.llm import LLMRouter

    # 重建路由器 = 按新配置重新建网关（网关是懒建的，没配的出口不会凭空多出
    # 一个 client）。⚠️ 副作用：用量统计随之清零 —— 与拆出口之前的行为一致。
    request.app.state.llm = LLMRouter(s)
    # UI 改的设置落盘（data/settings.json），重启后由 lifespan 覆盖回来。
    save_settings_overrides(s.data_dir / "settings.json", s)
    return {"ok": True}