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
    style: str = "克制写实，白描为主，少用形容词堆砌。"
    description_style: str = "以玩家五官可感知为限写景，心理描写只写玩家自己的。"
    banned_words: list[str] = Field(default_factory=list)
    writer_guidelines: str = ""  # 编剧准则：创作/排戏/写作要求合一（当前唯一编辑框）
    director_guidelines: str = ""  # legacy：合并前保留，非空时参与有效值
    storyteller_preset: str = ""  # legacy：合并前保留，非空时参与有效值

    @property
    def effective_writer_guidelines(self) -> str:
        """The single guidance block for the merged writer agent.

        Prefers the new unified field; falls back to the pre-merge boxes
        (director guidelines + storyteller preset) so old saves keep working.
        """
        if self.writer_guidelines:
            return self.writer_guidelines
        merged = f"{self.director_guidelines}\n\n{self.storyteller_preset}".strip()
        if merged:
            return merged
        return ""


class WorldContent(BaseModel):
    meta: WorldInfo
    lorebook: list[LoreEntry] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    npcs: dict[str, NpcCard] = Field(default_factory=dict)
    axes: list[Axis] = Field(default_factory=list)
    presets: NarrativePreset = Field(default_factory=NarrativePreset)