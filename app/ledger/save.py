from __future__ import annotations

from pydantic import BaseModel, Field

from app.world.models import NarrativePreset


class SaveMeta(BaseModel):
    world_id: str
    save_name: str
    created_at: str = ""
    next_event_id: int = 1


class EntityRuntime(BaseModel):
    lifecycle: str = "active"  # active | retired
    # has_actor / persona 全部在存档的世界实例（world/）人物卡里，运行时只有一个开关


class Hook(BaseModel):
    id: str
    text: str
    level: str = "npc"  # 主线钩子（world 级）预留：导演窗口阶段启用
    status: str = "open"  # open | closed（兑现）| expired（上限淘汰）
    opened_at: str = ""
    closed_at: str | None = None
    due: str | None = None  # 不解析到期（2026-09-05 拍板：避免时间解析的不可控问题）
    related: list[str] = Field(default_factory=list)


class PlayerProfile(BaseModel):
    """主角资料：与 NPC 人物卡基本一致（无 has_actor，无引擎调度）。

    private_note = 作者底牌（无人知道的真相，含主角自己也不知道的）；
    personal_secrets = 主角自知的隐秘/心结/前史（玩家心里的事）。
    """

    id: str = "player"
    name: str = "你"
    appearance: str = ""
    persona: str = ""
    private_note: str | None = None
    personal_secrets: str | None = None
    attributes: dict[str, int] = Field(default_factory=dict)  # 二期预留


class SaveData(BaseModel):
    meta: SaveMeta
    clock: str = ""
    player_scene: str = "main_street"
    player: PlayerProfile = Field(default_factory=PlayerProfile)
    narrative_preset: NarrativePreset = Field(default_factory=NarrativePreset)
    entities: dict[str, EntityRuntime] = Field(default_factory=dict)
    axes: dict[str, int] = Field(default_factory=dict)  # 二期预留
    hooks: list[Hook] = Field(default_factory=list)
    access_overrides: dict[str, list[str] | None] = Field(default_factory=dict)
    audit_last_error: str | None = None  # 最近一次审计失败记录（不再静默）
    active_lore_ids: list[str] = Field(default_factory=list)  # 本轮装配的世界书命中列表