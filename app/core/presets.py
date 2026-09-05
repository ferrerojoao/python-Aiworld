from __future__ import annotations

from pathlib import Path

from app.core.store import read_json, write_json_atomic
from app.world.models import NarrativePreset

DEFAULT_PRESET = NarrativePreset(
    writer_guidelines="克制写实，白描为主，少用形容词堆砌；优先让 NPC 主动制造冲突。",
    banned_words=[],
)


def load_global_preset(path: str | Path) -> NarrativePreset:
    path = Path(path)
    data = read_json(path, None)
    if data is None:
        save_global_preset(path, DEFAULT_PRESET)
        return DEFAULT_PRESET
    return NarrativePreset.model_validate(data)


def save_global_preset(path: str | Path, preset: NarrativePreset) -> None:
    write_json_atomic(Path(path), preset.model_dump())