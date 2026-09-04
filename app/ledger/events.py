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
    source: str = "turn"


class LocationFactRecord(BaseModel):
    id: str
    kind: Literal["location_fact"] = "location_fact"
    at: str
    subject: str
    location: str | None = None
    source: str = "witness"  # witness | event | override | director
    valid_until: str | None = None


class MemoRecord(BaseModel):
    id: str
    kind: Literal["memo"] = "memo"
    at: str
    ref: str
    note: str


EventRecord = NarrativeRecord | LocationFactRecord | MemoRecord