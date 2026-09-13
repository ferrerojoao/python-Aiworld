"""快速造世界：最小种子包（L1）与一句话草稿（L2）。

设计要点（2026-09-13）：

- **L1 种子**：一个可玩世界的下限是「1 个场景 + 1 张主角卡」——世界名、开局时刻
  都可以后补，唯独场景与主角卡缺了引擎就跑不动（主角位置由事件流水推导）。
- **L2 草稿**：LLM 只产**提案**，落在内存里先过 ``validate_assets`` 这道闸门，
  玩家看过、改过、点确认才落盘。所以本模块的产物与工作台编辑用的是**同一种
  资产形状**（overview/lorebook/scenes/npcs/axes），落盘一律复用
  ``save_world_assets``，不新增第二条写世界的路径。
"""

from __future__ import annotations

import shutil
import tempfile

from pydantic import BaseModel, Field

from .loader import check_world, save_world_assets

# 开局场景兜底名：表单没填、草稿也没给时用（作者可在工作台改名）。
DEFAULT_START_SCENE = "起点"


class DraftScene(BaseModel):
    id: str = ""  # 场景中文名（同时是事件 location 与显示名）
    aliases: list[str] = Field(default_factory=list)
    perceivable: str = ""
    region: str = ""


class DraftNpc(BaseModel):
    id: str = ""  # 人物中文名（同时是 participants 成员）
    appearance: str = ""
    persona: str = ""
    personal_secrets: str = ""  # 该角色自知的隐秘（只进他自己的 Actor 切片）
    private_note: str = ""  # 作者底牌（无人知道的真相，仅编剧可读）
    has_actor: bool = False  # 是否配 Actor（深抉择才派活）


class DraftLore(BaseModel):
    id: str = ""
    keywords: list[str] = Field(default_factory=list)
    body: str = ""
    always_on: bool = False


class WorldDraft(BaseModel):
    """L2 一句话草稿的 LLM 输出形状。

    字段全部有默认值：模型漏字段时退化成"更小的包"，而不是整次起草失败。
    主角卡不在 ``npcs`` 里——它由 ``player_name`` / ``player_*`` 单独给，
    转换时合成 ``is_player`` 卡，这样模型不会把主角写成普通 NPC。
    """

    name: str = ""
    summary: list[str] = Field(default_factory=list)  # 世界概要给编剧看的背景块
    opening: str = ""  # 开局白：落到新存档的事件日志第一条
    start_time: str = ""  # 世界钟起点（ISO）；空=引擎默认 2026-07-14T08:00:00
    start_scene: str = ""  # 开局场景（须为 scenes 里某个 id；转换时会纠偏）
    player_name: str = ""  # 主角真名（表单填了就以表单为准）
    player_appearance: str = ""
    player_persona: str = ""
    scenes: list[DraftScene] = Field(default_factory=list)
    npcs: list[DraftNpc] = Field(default_factory=list)
    lorebook: list[DraftLore] = Field(default_factory=list)


def blank_world_assets(
    world_id: str,
    name: str,
    player_name: str,
    start_scene: str = "",
    opening: str = "",
) -> dict:
    """L1 最小种子包：world.json + 1 个场景 + 主角卡 + 空的世界书/数值轴。

    开场白留空时写一句纪实句「<主角>来到<开局场景>。」——它不是占位符，而是
    主角在本世界的第一条**位置事实**：没有它，首轮工作单的在场名单里没有主角。
    """
    scene = (start_scene or "").strip() or DEFAULT_START_SCENE
    player = (player_name or "").strip()
    return {
        "overview": {
            "id": world_id,
            "name": (name or "").strip() or world_id,
            "summary": [],
            "opening": (opening or "").strip() or f"{player}来到{scene}。",
            "start_time": "",
            "start_scene": scene,
            "memory_limit": 50,
        },
        "lorebook": [],
        "scenes": [{"id": scene, "aliases": [], "perceivable": "", "region": ""}],
        "npcs": {player: {"id": player, "is_player": True}},
        "axes": [],
    }


def draft_to_assets(
    draft: WorldDraft,
    world_id: str,
    *,
    name: str = "",
    player_name: str = "",
    start_scene: str = "",
) -> dict:
    """草稿 → 工作台资产形状（``save_world_assets`` 的入参）。

    表单值优先于草稿值（玩家手填的 ID/世界名/主角名/开局场景是硬约束），
    并且顺手把三处越界纠正回合法态：空场景表、``start_scene`` 不在场景表里、
    主角卡缺失。所以「模型乱写」只会让包更小，不会让包坏掉。
    """
    scenes: list[dict] = []
    for item in draft.scenes:
        sid = (item.id or "").strip()
        if sid:
            scenes.append(
                {
                    "id": sid,
                    "aliases": [a for a in item.aliases if a.strip()],
                    "perceivable": item.perceivable,
                    "region": item.region,
                }
            )

    scene_id = (start_scene or "").strip() or (draft.start_scene or "").strip()
    if not scene_id or all(s["id"] != scene_id for s in scenes):
        scene_id = scenes[0]["id"] if scenes else DEFAULT_START_SCENE
    if not scenes:
        scenes = [{"id": scene_id, "aliases": [], "perceivable": "", "region": ""}]

    npcs: dict[str, dict] = {}
    pname = (player_name or "").strip() or (draft.player_name or "").strip()
    if pname:
        npcs[pname] = {
            "id": pname,
            "appearance": draft.player_appearance,
            "persona": draft.player_persona,
            "is_player": True,
        }
    for item in draft.npcs:
        nid = (item.id or "").strip()
        # 与主角撞名时保住主角卡：草稿里的同名条目当重复，丢弃。
        if not nid or nid in npcs:
            continue
        card: dict = {
            "id": nid,
            "appearance": item.appearance,
            "persona": item.persona,
            "has_actor": item.has_actor,
        }
        if item.personal_secrets:
            card["personal_secrets"] = item.personal_secrets
        if item.private_note:
            card["private_note"] = item.private_note
        npcs[nid] = card

    lore = [
        {
            "id": (entry.id or "").strip(),
            "keywords": [k for k in entry.keywords if k.strip()],
            "body": entry.body,
            "always_on": entry.always_on,
        }
        for entry in draft.lorebook
        if (entry.id or "").strip()
    ]

    return {
        "overview": {
            "id": world_id,
            "name": (name or "").strip() or draft.name.strip() or world_id,
            "summary": draft.summary,
            "opening": draft.opening,
            "start_time": draft.start_time,
            "start_scene": scene_id,
            "memory_limit": 50,
        },
        "lorebook": lore,
        "scenes": scenes,
        "npcs": npcs,
        "axes": [],
    }


def check_assets(assets: dict) -> list[str]:
    """在临时目录里试写一遍，再用 ``check_world`` 报**语义问题**。

    分两级（2026-09-13）：

    - **不变量级**（人物表恰好一张主角卡…）：``save_world_assets`` 直接抛
      ``ValueError``，本函数让它冒泡——这类错误存进去引擎就崩，调用方应当硬拦。
    - **语义问题**（``start_scene`` 越界 / 场景 id 重复 / 世界书条目没关键词…）：
      返回清单，调用方可以只警告——"中途拆结构"的半成品态是合法的工作中状态。

    两级的差别只在**调用方怎么处理**，体检动作本身是同一套。真实世界目录不受影响。
    """
    tmp = tempfile.mkdtemp(prefix="aiworld-draft-")
    try:
        save_world_assets(tmp, assets)
        return check_world(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def validate_assets(assets: dict) -> list[str]:
    """硬 + 软合一：任何问题都返回清单（用于"世界还不存在"的场合，一律拦）。

    ``POST /worlds/new`` 用它——那里没有存档可回退，坏包一步都不该落盘。
    """
    try:
        return check_assets(assets)
    except ValueError as exc:  # 不变量失败也当问题报出来
        return [str(exc)]
