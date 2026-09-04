from __future__ import annotations

from pydantic import BaseModel, Field


class Beat(BaseModel):
    kind: str = "narrate"
    text: str = ""
    speaker: str | None = None
    meaning: str | None = None
    tone_hint: str | None = None
    actor_npc_id: str | None = None
    resolved: dict | None = None


class Directive(BaseModel):
    mode: str = "scene"  # scene | adopt_player_body
    beats: list[Beat] = Field(default_factory=list)
    lore_refs: list[str] = Field(default_factory=list)
    motivation_note: str = ""
    adopt_player_body: bool = False
    private: bool = False
    location: str | None = None
    participants: list[str] = Field(default_factory=list)


class StoryOutput(BaseModel):
    prose: str
    time_hint: dict | None = None


class QCOutput(BaseModel):
    status: str = "pass"  # pass | fixed | conflict
    prose: str = ""
    issues: list[dict] = Field(default_factory=list)


class ActorDecision(BaseModel):
    decision: str = ""
    action_hint: str = ""
    tone: str = ""


class AuditOutput(BaseModel):
    memo: str | None = None
    hook_texts: list[str] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)