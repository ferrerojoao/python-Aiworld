from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NarrativeRecord(BaseModel):
    id: str
    kind: Literal["narrative"] = "narrative"
    at: str
    location: str | None = None
    participants: list[str] = Field(default_factory=list)
    known_by: list[str] | None = None  # None means public
    body: str
    summary: str | None = None
    source: str = "turn"


EventRecord = NarrativeRecord