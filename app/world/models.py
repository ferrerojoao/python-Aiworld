from __future__ import annotations

from pydantic import BaseModel, Field


class WorldInfo(BaseModel):
    id: str
    name: str
    summary: list[str] = Field(default_factory=list)
    opening: str = ""  # opening prose: becomes the first ledger event of a save
    start_time: str = ""  # 世界钟起点（ISO 时间）；空=回退引擎默认 2026-07-14T08:00:00
    memory_limit: int = Field(default=50, ge=0)  # experiences 回溯条数上限（0=不给记忆）


class LoreEntry(BaseModel):
    """World book entry: a background concept triggered by keyword hits.

    世界书 = 概念化背景条目：keywords 命中玩家输入/已采纳正文时进入
    本回合装配；body 是命中后发给编剧的全文（无摘要层）。
    """

    id: str
    keywords: list[str] = Field(default_factory=list)
    body: str = ""
    always_on: bool = Field(default=False)  # 常驻开关：勾选后每轮必注入，不占关键词触发名额


class Scene(BaseModel):
    """场景键 = 中文名（id 即显示名，审计/事件/工作单直接用中文，无独立英文名）。"""

    id: str  # 场景中文名，如"鱼市"（同时是事件 location 与显示名）
    aliases: list[str] = Field(default_factory=list)  # 变体名（"码头鱼市"），给移动解析与 id 纠偏
    perceivable: str = ""
    region: str = ""  # 消息域：该场景事件归属的地域（跨区域知识边界用）；空 = 全域公共区


class NpcCard(BaseModel):
    """人物卡键 = 中文名（id 即显示名，participants/事件/工作单直接用中文）。"""

    id: str  # NPC 中文名，如"朱明"（同时是 participants 成员与显示名）
    appearance: str = ""
    persona: str = ""
    private_note: str | None = None  # 作者底牌：无人（含 NPC 自己）知道的真相，仅编剧可读
    personal_secrets: str | None = None  # 该 NPC 自知的隐秘（心结/往事/把柄），进他自己的 Actor 切片
    has_actor: bool = False
    region: list[str] = Field(default_factory=list)  # 消息域（听域/来属地，可多个）；空 = 按亲历事件推导


class Axis(BaseModel):
    id: str
    label: str = ""
    tags: list[str] = Field(default_factory=list)
    target: str | None = None
    range: tuple[int, int] = (-100, 100)
    init: int = 0
    visible: bool = True
    track_cause: bool = False


class NarrativePreset(BaseModel):
    """Global narrative preset for the merged writer agent (single box).

    writer_guidelines: the one guidance block (scene shaping + prose style).
    banned_words: hard-blocked words enforced by the QC pass.
    """

    writer_guidelines: str = ""
    banned_words: list[str] = Field(default_factory=list)


class WorldContent(BaseModel):
    meta: WorldInfo
    lorebook: list[LoreEntry] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    npcs: dict[str, NpcCard] = Field(default_factory=dict)
    axes: list[Axis] = Field(default_factory=list)
    presets: NarrativePreset = Field(default_factory=NarrativePreset)