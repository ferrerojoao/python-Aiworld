from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.store import new_id
from app.world.models import NarrativePreset


class SaveMeta(BaseModel):
    world_id: str
    save_name: str
    created_at: str = ""
    next_event_id: int = 1


class StateItem(BaseModel):
    """角色状态 · 长期事实（REQ 〇章「角色状态」；叙事事实类）。

    "他此刻是什么"——免疫普通武器、独臂、病倒。**引擎一行都不读**
    （唯一职能是让编剧与审计知道），所以它是纯文本事实，不是可结算的数值
    （那是 ③ 抽象属性的活）。判据：新加属性先问"引擎会不会读它"——会 = 独立
    字段（如 ``lifecycle``），不会 = 本类条目。

    ``text`` 一行事实，直接进提示词。**只写"他此刻是什么"，不写"他怎么变成这样的"**
    （2026-09-15 用户拍板）：一条状态 + 一个布尔表达不了"成因私密、表现公开"两层——
    "沐浴龙血，普通刀剑伤不了他"标 public=true 会把成因一起交出去，标 false 又连表现
    一起藏起来。所以状态只承载**表现**（"普通刀剑伤不了他"），成因留在产生它的那条
    事件里（``source_event`` 指得着）；``public`` 只表示**这条状态**旁人看不看得见
    （"左腿瘸了"看得见 / "其实色盲"看不见）——决定**别人**的 NPC Actor 拿不拿
    （本人那一条全给：``public`` 说的是旁人，对本人没有意义）；``until`` 到期失效
    （"病倒三天"不是永久事实）。

    **到期不再删条目**（2026-09-14 晚改）：失效时刻记进 ``expired_at``，条目留在
    存档里——注入侧按它过滤，玩家在面板上仍看得见、仍撤得掉。旧行为（到点 pop 掉）
    让"审计单方面给了期限"变成一条玩家无从追溯、也无从撤销的静默删除。

    ``id``（2026-09-14 加）：审计**移除**状态时的精确定位（它看不见 id 就只会
    按原文猜，"骨裂"与"左臂骨裂"必有一场误伤）。默认工厂保证手塞条目与旧存档
    也有 id（pydantic 只在键缺失时调用工厂），所以**不需要迁移**。编剧侧不注入 id。
    """

    id: str = Field(default_factory=lambda: new_id("st"))
    text: str = ""
    since: str = ""  # 获得时的世界钟（ISO）
    source_event: str = ""  # 哪条正文给的 → 可追溯 / 可撤销
    public: bool = False  # 这条状态旁人看得见吗（**别人的** Actor 只拿 True 的；本人全给）
    until: str = ""  # 可选：到期失效（空 = 永久，直到审计结束或玩家撤销）
    expired_at: str = ""  # 非空 = 已失效（失效时刻）；注入侧一律过滤，条目本身留着


class EntityRuntime(BaseModel):
    lifecycle: str = "active"  # active | retired
    # 角色状态 · 长期事实（2026-09-14）：**引擎开关类**只有上面那个 lifecycle
    # （present_at 会读它）；这里是**叙事事实类**，只有 LLM 消费。两类不合并——
    # 合并等于让 LLM 去猜"这条要不要影响引擎行为"。
    # 存在存档而不是人物卡：与存档同生命周期，重置世界即清零。
    states: list[StateItem] = Field(default_factory=list)
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
    # 最近一次采纳的角色状态变更（Step 2，2026-09-14）：added / removed / expired /
    # skipped（含跳过原因）。误加一条长期事实的代价高（它会改变此后每一轮提示词），
    # 所以"编剧为什么认为我免疫刀剑"必须查得出来。**只留最近一次**——完整历史在
    # 事件流的记账事件里（每条状态变更都落一条，用 source_event 对得上）。
    last_state_change: dict | None = None