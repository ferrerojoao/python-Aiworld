from __future__ import annotations

from pydantic import BaseModel, Field


class WorldInfo(BaseModel):
    id: str
    name: str
    summary: list[str] = Field(default_factory=list)
    opening: str = ""  # opening prose: becomes the first ledger event of a save
    default_durations: dict[str, int] = Field(default_factory=dict)
    author_banned_words: list[str] = Field(default_factory=list)


class LoreEntry(BaseModel):
    id: str
    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    body: str = ""


class Scene(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    perceivable: str = ""
    open_hours: str = "全天"
    adjacent: list[str] = Field(default_factory=list)


class NpcCard(BaseModel):
    id: str
    name: str
    appearance: str = ""
    persona: str = ""
    private_note: str | None = None
    has_actor: bool = False
    normal_schedule: str | None = None


class Axis(BaseModel):
    id: str
    label: str = ""
    tags: list[str] = Field(default_factory=list)
    target: str | None = None
    range: tuple[int, int] = (-100, 100)
    init: int = 0
    visible: bool = True
    track_cause: bool = False


class NarrativePreset(BaseModel):
    """Global narrative preset for the merged writer agent (single box).

    writer_guidelines: the one guidance block (scene shaping + prose style).
    banned_words: hard-blocked words enforced by the QC pass.
    """

    writer_guidelines: str = ""
    banned_words: list[str] = Field(default_factory=list)


class WorldContent(BaseModel):
    meta: WorldInfo
    lorebook: list[LoreEntry] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    npcs: dict[str, NpcCard] = Field(default_factory=dict)
    axes: list[Axis] = Field(default_factory=list)
    presets: NarrativePreset = Field(default_factory=NarrativePreset)