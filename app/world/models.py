from __future__ import annotations

from pydantic import BaseModel, Field

# 世界包没写主角卡时由 load_world 合成的占位名（作者在人物编辑里改名即可）。
PLAYER_PLACEHOLDER = "主角"

# 自动注册场景的感知描述占位（``Ledger.register_scene``）。**引擎不代笔**——
# 由审计顺手编一句"空气里有霉味"看着像设定，进 world 资产后每轮注入编剧与 QC，
# 作者日后分不清哪句是自己写的。留个明说"没有"的短句，等人在工作台补（2026-09-16）。
# 必须是一句能独立成句的话：``scene_description`` 把它夹在"这里是X。"与"在场：…"
# 之间直接拼（写成"暂无"会拼出"这里是家。暂无四下无人。"这种病句）。
SCENE_PLACEHOLDER = "暂无描述。"


class WorldInfo(BaseModel):
    id: str
    name: str
    summary: list[str] = Field(default_factory=list)
    opening: str = ""  # opening prose: becomes the first ledger event of a save
    start_time: str = ""  # 世界钟起点（ISO 时间）；空=回退引擎默认 2026-07-14T08:00:00
    start_scene: str = ""  # 开局场景（中文名，须为已注册场景）；空=取第一个注册场景


class LoreEntry(BaseModel):
    """World book entry: a background concept triggered by keyword hits.

    世界书 = 概念化背景条目：keywords 命中玩家输入/已采纳正文时进入
    本回合装配；body 是命中后发给编剧的全文（无摘要层）。
    """

    id: str
    keywords: list[str] = Field(default_factory=list)
    body: str = ""
    always_on: bool = Field(default=False)  # 常驻开关：勾选后每轮必注入，不占关键词触发名额
    # 归属角色（人物表中文名，2026-09-13）：该角色**在场**即注入，不依赖正文点名——
    # "一个 NPC 的信息 = 卡 + 归属条目"才齐全。空串 = 纯关键词触发。每人上限
    # 见 app.rules.lorebook.LORE_SUBJECT_CAP（注入侧硬截断，体检侧报警告）。
    subject: str = ""


class Scene(BaseModel):
    """场景键 = 中文名（id 即显示名，审计/事件/工作单直接用中文，无独立英文名）。"""

    id: str  # 场景中文名，如"鱼市"（同时是事件 location 与显示名）
    aliases: list[str] = Field(default_factory=list)  # 变体名（"码头鱼市"），给移动解析与 id 纠偏
    perceivable: str = ""
    region: str = ""  # 消息域：该场景事件归属的地域（跨区域知识边界用）；空 = 全域公共区


class NpcCard(BaseModel):
    """人物卡键 = 中文名（id 即显示名，participants/事件/工作单直接用中文）。

    主角是人物表里的普通一员（2026-09-13 用户拍板）：靠 ``is_player`` 认出，
    不再有独立的 PlayerProfile。事件日志直接记人名 → 中途换主角不影响旧日志。
    """

    id: str  # NPC 中文名，如"朱明"（同时是 participants 成员与显示名）
    appearance: str = ""
    persona: str = ""
    private_note: str | None = None  # 作者底牌：无人（含 NPC 自己）知道的真相，仅编剧可读
    personal_secrets: str | None = None  # 该 NPC 自知的隐秘（心结/往事/把柄），进他自己的 Actor 切片
    has_actor: bool = False
    is_player: bool = False  # 主角标记：全表有且仅有一张
    region: list[str] = Field(default_factory=list)  # 消息域（听域/来属地，可多个）；空 = 按亲历事件推导
    attributes: dict[str, int] = Field(default_factory=dict)  # 二期预留


class NarrativePreset(BaseModel):
    """Global narrative preset for the merged writer agent (single box).

    writer_guidelines: the one guidance block (scene shaping + prose style).
    banned_words: hard-blocked words enforced by the QC pass.
    style_sample: 文风示范原文（三级块内随禁令注入；空 = 不注入）。
    """

    writer_guidelines: str = ""
    banned_words: list[str] = Field(default_factory=list)
    style_sample: str = ""  # 文风示范原文；空 = 不注入


class WorldContent(BaseModel):
    meta: WorldInfo
    lorebook: list[LoreEntry] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    npcs: dict[str, NpcCard] = Field(default_factory=dict)
    presets: NarrativePreset = Field(default_factory=NarrativePreset)

    # ------------------------------------------------------------------
    # 主角（人物表里的普通一员，2026-09-13）
    # ------------------------------------------------------------------
    def player(self) -> NpcCard | None:
        """主角卡：人物表里 ``is_player`` 的那一张（load_world 保证存在）。"""
        for card in self.npcs.values():
            if card.is_player:
                return card
        return None

    def player_name(self) -> str:
        """主角名 = 人物表键 = 事件日志里记的名字。"""
        card = self.player()
        return card.id if card is not None else PLAYER_PLACEHOLDER

    def start_scene_id(self) -> str:
        """开局场景：``start_scene`` 命中注册场景才作数，否则取第一个注册场景。"""
        if self.meta.start_scene and any(s.id == self.meta.start_scene for s in self.scenes):
            return self.meta.start_scene
        return self.scenes[0].id if self.scenes else ""