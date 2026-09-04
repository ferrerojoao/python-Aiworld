from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field


class Beat(BaseModel):
    kind: str = Field(default="narrate", validation_alias=AliasChoices("kind", "类型", "种类"))
    text: str = Field(default="", validation_alias=AliasChoices("text", "content", "body", "正文", "内容", "台词", "文本"))
    speaker: str | None = Field(default=None, validation_alias=AliasChoices("speaker", "角色", "说话者"))
    meaning: str | None = Field(default=None, validation_alias=AliasChoices("meaning", "含义", "意图"))
    tone_hint: str | None = Field(default=None, validation_alias=AliasChoices("tone_hint", "tone", "语气"))
    actor_npc_id: str | None = Field(default=None, validation_alias=AliasChoices("actor_npc_id", "npc_id", "角色ID", "NPC"))
    resolved: dict | None = None


class Directive(BaseModel):
    mode: str = Field(default="scene", validation_alias=AliasChoices("mode", "模式", "方式"))
    beats: list[Beat] = Field(default_factory=list, validation_alias=AliasChoices("beats", "节拍", "指令", "段落", "场次"))
    lore_refs: list[str] = Field(default_factory=list, validation_alias=AliasChoices("lore_refs", "world_refs", "世界书引用", "引用"))
    motivation_note: str = Field(default="", validation_alias=AliasChoices("motivation_note", "动机", "幕后注"))
    adopt_player_body: bool = Field(default=False, validation_alias=AliasChoices("adopt_player_body", "采用玩家正文"))
    private: bool = Field(default=False, validation_alias=AliasChoices("private", "私密"))
    location: str | None = Field(default=None, validation_alias=AliasChoices("location", "地点", "场景"))
    participants: list[str] = Field(default_factory=list, validation_alias=AliasChoices("participants", "参与人", "在场者"))


class StoryOutput(BaseModel):
    prose: str = Field(
        validation_alias=AliasChoices(
            "prose", "body", "text", "title", "content", "output", "result", "story", "narrative",
            "narrate", "speech", "narration", "scene", "play",
            "正文", "内容", "输出", "结果", "回复", "文本", "故事", "叙述", "生成结果", "旁白", "描写",
        )
    )
    time_hint: dict | None = Field(default=None, validation_alias=AliasChoices("time_hint", "time", "时间"))


class QCOutput(BaseModel):
    status: str = Field(
        default="pass",
        validation_alias=AliasChoices("status", "result", "verdict", "状态", "结果"),
    )
    prose: str = Field(
        default="",
        validation_alias=AliasChoices(
            "prose", "body", "text", "content", "output", "revised", "fixed_text",
            "正文", "内容", "输出", "结果", "回复", "文本", "修订",
        ),
    )
    issues: list[dict] = Field(default_factory=list, validation_alias=AliasChoices("issues", "问题", "矛盾"))


class ActorDecision(BaseModel):
    decision: str = Field(default="", validation_alias=AliasChoices("decision", "决定", "决策"))
    action_hint: str = Field(default="", validation_alias=AliasChoices("action_hint", "行动", "行为提示"))
    tone: str = Field(default="", validation_alias=AliasChoices("tone", "语气"))


class AuditOutput(BaseModel):
    hook_texts: list[str] = Field(default_factory=list, validation_alias=AliasChoices("hook_texts", "hooks", "钩子"))
    conflicts: list[dict] = Field(default_factory=list, validation_alias=AliasChoices("conflicts", "矛盾"))