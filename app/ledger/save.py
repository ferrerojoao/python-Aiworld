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


class Goal(BaseModel):
    """剧情目标（M14 主线载体）：导演窗口设立、审计按正文判定完成、
    编剧写作时向目标引导。钩子台账已废弃（2026-09-07）。

    kind: big=大目标（主线）/ small=小目标（支线/节点）
    subject: 目标归属者——"player"=玩家的目标；"npc_xxx"=该 NPC 的目标
             （由玩家与导演讨论时替 NPC 设立，剧情里由该 NPC 主动推进）
    status: active | done | abandoned
    big_goal_id: 小目标挂靠的大目标（仅展示层级，不参与判定）
    npc_id: 关联 NPC（引导素材/主动登门）
    """

    id: str
    text: str
    kind: str = "small"  # big | small
    subject: str = "player"  # player | npc_xxx（目标归属者）
    status: str = "active"  # active | done | abandoned
    big_goal_id: str | None = None
    npc_id: str | None = None
    created_at: str = ""
    done_at: str | None = None


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
    scene_name: str = ""  # 当前场景显示名：注册场景取场景表，一次性场景取审计中文名（回退 player_scene）
    player: PlayerProfile = Field(default_factory=PlayerProfile)
    narrative_preset: NarrativePreset = Field(default_factory=NarrativePreset)
    entities: dict[str, EntityRuntime] = Field(default_factory=dict)
    axes: dict[str, int] = Field(default_factory=dict)  # 二期预留
    access_overrides: dict[str, list[str] | None] = Field(default_factory=dict)
    audit_last_error: str | None = None  # 最近一次审计失败记录（不再静默）
    active_lore_ids: list[str] = Field(default_factory=list)  # 本轮装配的世界书命中列表
    goals: list[Goal] = Field(default_factory=list)  # 剧情目标（M14，替代钩子台账）