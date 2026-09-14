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
    subject: 目标归属者——空串=主角的目标（默认）；否则是该角色的中文名
             （由玩家与导演讨论时替 NPC 设立，剧情里由该 NPC 主动推进）
    status: active | done | abandoned
    big_goal_id: 小目标挂靠的大目标（仅展示层级，不参与判定）
    npc_id: 关联 NPC（引导素材/主动登门）
    """

    id: str
    text: str
    kind: str = "small"  # big | small
    subject: str = ""  # ""=主角；否则角色中文名（归属者）
    status: str = "active"  # active | done | abandoned
    big_goal_id: str | None = None
    npc_id: str | None = None
    created_at: str = ""
    done_at: str | None = None


class SaveData(BaseModel):
    meta: SaveMeta
    clock: str = ""
    # 镜头位置：与主角最近所在场景一致（主角自身的位置事实在事件流水里，
    # 由 present_at 推导；此处是持久化的小抄，2026-09-13 保留原语义）。
    player_scene: str = ""
    narrative_preset: NarrativePreset = Field(default_factory=NarrativePreset)
    entities: dict[str, EntityRuntime] = Field(default_factory=dict)
    axes: dict[str, int] = Field(default_factory=dict)  # 二期预留
    access_overrides: dict[str, list[str] | None] = Field(default_factory=dict)
    audit_last_error: str | None = None  # 最近一次审计失败记录（不再静默）
    active_lore_ids: list[str] = Field(default_factory=list)  # 本轮装配的世界书命中列表
    goals: list[Goal] = Field(default_factory=list)  # 剧情目标（M14，替代钩子台账）
    # 最近一次采纳的时间结算留痕（P1 可观测性，2026-09-14）：审计结算结果原本
    # 算完即丢，玩家看到时钟跳了几小时查不出原因。整条优先级链落在这里
    # （clock_before/after、delta_rule、delta_audit、source、对钟是否被拒），
    # 由 /state 透出给顶栏时钟 hover。诊断用，不参与任何逻辑判定。
    last_settlement: dict | None = None