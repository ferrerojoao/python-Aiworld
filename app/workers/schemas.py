from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class ActorQuestion(BaseModel):
    """A deep choice the writer cannot decide for itself: delegate to the NPC's
    isolated Actor after the writer pass, then re-write once with the result."""

    npc_id: str = Field(validation_alias=AliasChoices("npc_id", "角色ID", "NPC"))
    question: str = Field(default="", validation_alias=AliasChoices("question", "问题", "深抉择"))
    context: str = Field(default="", validation_alias=AliasChoices("context", "情境"))


class WriterOutput(BaseModel):
    """Single-agent output: the writer (director + storyteller in one) decides
    the scene shape and writes the prose in one call.

    The model is told to plan beats mentally, then write directly. When a
    present NPC with an actor ticket faces a deep choice, the model emits
    actor_questions instead of guessing the NPC's mind; the engine runs the
    isolated Actor and re-invokes the writer once with the decisions.
    """

    prose: str = Field(
        validation_alias=AliasChoices(
            "prose", "body", "text", "title", "content", "output", "result", "story", "narrative",
            "narrate", "speech", "narration", "scene", "play",
            "正文", "内容", "输出", "结果", "回复", "文本", "故事", "叙述", "生成结果", "旁白", "描写",
        )
    )
    time_hint: dict | None = Field(default=None, validation_alias=AliasChoices("time_hint", "time", "时间"))
    summary: str = Field(default="", validation_alias=AliasChoices("summary", "摘要"))
    location: str | None = Field(default=None, validation_alias=AliasChoices("location", "地点", "场景"))
    participants: list[str] = Field(default_factory=list, validation_alias=AliasChoices("participants", "参与人", "在场者"))
    private: bool = Field(default=False, validation_alias=AliasChoices("private", "私密"))
    adopt_player_body: bool = Field(default=False, validation_alias=AliasChoices("adopt_player_body", "采用玩家正文"))
    actor_questions: list[ActorQuestion] = Field(
        default_factory=list,
        validation_alias=AliasChoices("actor_questions", "deep_choices", "深抉择", "需要NPC决策"),
    )


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


class ActorDecision(BaseModel):
    decision: str = Field(default="", validation_alias=AliasChoices("decision", "决定", "决策"))
    action_hint: str = Field(default="", validation_alias=AliasChoices("action_hint", "行动", "行为提示"))
    tone: str = Field(default="", validation_alias=AliasChoices("tone", "语气"))


class AuditOutput(BaseModel):
    hook_texts: list[str] = Field(default_factory=list, validation_alias=AliasChoices("hook_texts", "hooks", "钩子"))
    lifecycle: list[dict] = Field(
        default_factory=list,
        validation_alias=AliasChoices("lifecycle", "lifecycle_changes", "生命周期"),
    )