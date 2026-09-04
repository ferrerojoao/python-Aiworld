from __future__ import annotations

from pathlib import Path

from app.core.store import read_json, write_json_atomic
from app.world.models import NarrativePreset

DEFAULT_PRESET = NarrativePreset(
    style="克制写实，白描为主，少用形容词堆砌。",
    description_style="以玩家五官可感知为限写景，心理描写只写玩家自己的。",
    banned_words=[],
    pace="slow",
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