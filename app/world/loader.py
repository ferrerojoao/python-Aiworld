from __future__ import annotations

import json
from pathlib import Path

from app.core.store import write_json_atomic
from .models import Axis, LoreEntry, NarrativePreset, NpcCard, Scene, WorldContent, WorldInfo


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_world(root: str | Path) -> WorldContent:
    """Load a content package from a directory.

    Missing optional files are treated as empty/default values. Required
    fields are validated through Pydantic.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"world content directory not found: {root}")

    raw_world = _read_json(root / "world.json", {})
    meta = WorldInfo.model_validate(raw_world)

    raw_lore = _read_json(root / "lorebook.json", [])
    lorebook = [LoreEntry.model_validate(item) for item in raw_lore]

    raw_scenes = _read_json(root / "scenes.json", [])
    scenes = [Scene.model_validate(item) for item in raw_scenes]

    npcs: dict[str, NpcCard] = {}
    npc_dir = root / "npcs"
    if npc_dir.is_dir():
        for path in sorted(npc_dir.glob("*.json")):
            card = NpcCard.model_validate(_read_json(path, {}))
            npcs[card.id] = card

    raw_axes = _read_json(root / "axes.json", [])
    axes = [Axis.model_validate(item) for item in raw_axes]

    raw_presets = _read_json(root / "presets.json", {})
    presets = NarrativePreset.model_validate(raw_presets)

    return WorldContent(
        meta=meta,
        lorebook=lorebook,
        scenes=scenes,
        npcs=npcs,
        axes=axes,
        presets=presets,
    )


def check_world(root: str | Path) -> list[str]:
    """Return a list of validation problems. Empty list means OK."""
    problems: list[str] = []
    try:
        world = load_world(root)
    except Exception as exc:  # pragma: no cover - diagnostic helper
        return [f"world load failed: {exc}"]

    scene_ids = {s.id for s in world.scenes}
    for scene in world.scenes:
        for adj in scene.adjacent:
            if adj not in scene_ids:
                problems.append(f"scene {scene.id}: unknown adjacent {adj}")
    for npc_id in world.npcs:
        if not npc_id.startswith("npc_"):
            problems.append(f"npc id should start with 'npc_': {npc_id}")
    return problems


def save_world_assets(root: str | Path, data: dict) -> None:
    """Write world asset files from editor payload.

    data keys: overview, lorebook, scenes, npcs, axes
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    overview = data.get("overview") or {}
    world_info = {
        "id": overview.get("id", root.name),
        "name": overview.get("name", root.name),
        "summary": overview.get("summary", []),
        "default_durations": overview.get("default_durations", {}),
    }
    write_json_atomic(root / "world.json", world_info)
    write_json_atomic(root / "lorebook.json", data.get("lorebook", []))
    write_json_atomic(root / "scenes.json", data.get("scenes", []))
    write_json_atomic(root / "axes.json", data.get("axes", []))

    npcs_dir = root / "npcs"
    npcs_dir.mkdir(exist_ok=True)
    for old in npcs_dir.glob("*.json"):
        old.unlink()
    for npc_id, card in (data.get("npcs") or {}).items():
        write_json_atomic(npcs_dir / f"{npc_id}.json", card)


if __name__ == "__main__":  # pragma: no cover
    import sys

    root = sys.argv[1] if len(sys.argv) > 1 else "content/qinghsi"
    problems = check_world(root)
    if problems:
        print("校验失败：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print(f"OK: {root}")