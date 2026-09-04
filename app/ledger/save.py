from __future__ import annotations

from pydantic import BaseModel, Field

from app.world.models import NarrativePreset, Scene


class SaveMeta(BaseModel):
    world_id: str
    save_name: str
    created_at: str = ""
    next_event_id: int = 1


class EntityRuntime(BaseModel):
    lifecycle: str = "active"  # active | retired
    has_actor: bool = False
    forced_actor: bool = False
    persona_patch: str | None = None


class Hook(BaseModel):
    id: str
    text: str
    level: str = "npc"
    status: str = "open"
    opened_at: str = ""
    due: str | None = None
    related: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    id: str
    at: str = ""
    level: str = "major"
    desc: str = ""
    status: str = "open"
    ref: str | None = None


class SaveData(BaseModel):
    meta: SaveMeta
    clock: str = ""
    player_scene: str = "main_street"
    narrative_preset: NarrativePreset = Field(default_factory=NarrativePreset)
    entities: dict[str, EntityRuntime] = Field(default_factory=dict)
    axes: dict[str, int] = Field(default_factory=dict)  # 二期预留
    hooks: list[Hook] = Field(default_factory=list)
    pending_conflicts: list[Conflict] = Field(default_factory=list)
    scene_addons: dict[str, Scene] = Field(default_factory=dict)
    access_overrides: dict[str, list[str] | None] = Field(default_factory=dict)