from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class ActorQuestion(BaseModel):
    """A deep choice the writer cannot decide for itself: delegate to the NPC's
    isolated Actor after the writer pass, then re-write once with the result."""

    npc_id: str = Field(validation_alias=AliasChoices("npc_id", "角色ID", "NPC"))
    question: str = Field(default="", validation_alias=AliasChoices("question", "问题", "深抉择"))
    context: str = Field(default="", validation_alias=AliasChoices("context", "情境"))


class WriterOutput(BaseModel):
    """Writer output: prose only. World side effects (time/location/presence/
    privacy, plus one-shot scene handling) are inferred by the audit at adopt
    time — the writer never reports bookkeeping fields."""

    prose: str = Field(
        validation_alias=AliasChoices(
            "prose", "body", "text", "title", "content", "output", "result", "story", "narrative",
            "narrate", "speech", "narration", "scene", "play",
            "正文", "内容", "输出", "结果", "回复", "文本", "故事", "叙述", "生成结果", "旁白", "描写",
        )
    )
    summary: str = Field(default="", validation_alias=AliasChoices("summary", "摘要"))
    actor_questions: list[ActorQuestion] = Field(
        default_factory=list,
        validation_alias=AliasChoices("actor_questions", "deep_choices", "深抉择", "需要NPC决策"),
    )


class DirectorAction(BaseModel):
    """A backstage write the director LLM proposes; the player must confirm
    before the engine executes it (two-stage gate, REQ §三章 幕后事务)."""

    type: str = Field(validation_alias=AliasChoices("type", "action", "操作"))
    payload: dict = Field(default_factory=dict, validation_alias=AliasChoices("payload", "参数"))


class DirectorReply(BaseModel):
    """Structured director-window reply: plain text + optional pending action."""

    reply: str = Field(default="", validation_alias=AliasChoices("reply", "answer", "回复", "回答"))
    action: DirectorAction | None = Field(default=None, validation_alias=AliasChoices("action", "操作", "待确认"))


class QCOutput(BaseModel):
    status: str = Field(
        default="pass",
        validation_alias=AliasChoices("status", "verdict", "result", "状态", "结果"),
    )
    prose: str = Field(
        default="",
        validation_alias=AliasChoices(
            "prose", "body", "text", "content", "output", "revised", "fixed_text",
            "正文", "内容", "输出", "回复", "文本", "修订",
        ),
    )
    issues: list[dict] = Field(default_factory=list, validation_alias=AliasChoices("issues", "问题", "修改记录"))
    summary: str = Field(
        default="",
        validation_alias=AliasChoices("summary", "摘要"),
    )


class ActorDecision(BaseModel):
    decision: str = Field(default="", validation_alias=AliasChoices("decision", "决定", "决策"))
    action_hint: str = Field(default="", validation_alias=AliasChoices("action_hint", "行动", "行为提示"))
    tone: str = Field(default="", validation_alias=AliasChoices("tone", "语气"))


class AuditOutput(BaseModel):
    """Audit settlement: world side effects inferred from the prose, plus
    goal completion and lifecycle. Runs at adopt; returns what to write into
    the ledger."""

    location: str | None = Field(default=None, validation_alias=AliasChoices("location", "地点", "场景"))
    scene_name: str = Field(default="", validation_alias=AliasChoices("scene_name", "地点名", "场景名"))
    register_scene: bool = Field(default=False, validation_alias=AliasChoices("register_scene", "注册场景"))
    participants: list[str] = Field(default_factory=list, validation_alias=AliasChoices("participants", "在场者"))
    # 出场者：本轮**与玩家有往来、且有名字**的人（玩家开口问他 / 他开口答话 /
    # 有名字的第三方转述等实质互动都算；只是"布景里杵着"不算）。与 participants
    # 的区别是本质的：在场是几何位置（可继承），出场是"这一轮真的碰上了"
    # （不可继承，必须由本轮正文产生）。引擎用它给"未落卡的确定人物"计数授键。
    featured: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("featured", "出场", "出场者"),
    )
    private: bool = Field(default=False, validation_alias=AliasChoices("private", "私密"))
    delta_minutes: int = Field(default=0, validation_alias=AliasChoices("delta_minutes", "推进分钟", "时间推进"))
    completed_goal_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("completed_goal_ids", "completed_goals", "完成目标"),
    )
    lifecycle: list[dict] = Field(
        default_factory=list,
        validation_alias=AliasChoices("lifecycle", "lifecycle_changes", "生命周期"),
    )
    npc_moves: list[dict] = Field(
        default_factory=list,
        validation_alias=AliasChoices("npc_moves", "npc移动", "角色去向"),
    )
    clock_to: str = Field(
        default="",
        validation_alias=AliasChoices("clock_to", "目标时间", "对钟", "绝对时间"),
    )
    # 角色状态 · 长期事实（REQ 〇章；Step 2，2026-09-14）。
    # 两个字段都用**宽松的裸 list**：一条格式歪掉的条目不该让整次审计结算失败
    # （validation 失败 → audit_error → 时间/位置/目标全部落不了地）。
    # 具体归一只在 Transaction 落地时做（形状不对的条目进 skipped 留痕）。
    state_add: list = Field(
        default_factory=list,
        validation_alias=AliasChoices("state_add", "states_add", "新增状态", "状态新增"),
    )
    state_remove: list = Field(
        default_factory=list,
        validation_alias=AliasChoices("state_remove", "states_remove", "移除状态", "状态移除"),
    )