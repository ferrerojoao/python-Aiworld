from __future__ import annotations

from pathlib import Path

from app.ledger.queries import Ledger
from app.ledger.save import SaveData
from app.runtime.transaction import CandidateStore
from app.world.loader import load_world
from app.world.models import WorldContent


class GameSession:
    def __init__(self, world: WorldContent, save_dir: str | Path):
        self.world = world
        self.save_dir = Path(save_dir)
        self.ledger = Ledger(world, save_dir)
        self.candidates = CandidateStore(self.save_dir / "candidates")
        self.director_history: list[dict] = []

    @property
    def sid(self) -> str:
        return f"{self.ledger.save.meta.world_id}:{self.ledger.save.meta.save_name}"

    def scene_description(self) -> str:
        from app.rules.scenes import scene_description

        return scene_description(self.ledger.save.player_scene, self.world, self.ledger)


def create_session(world_root: str | Path, save_root: str | Path, save_name: str) -> GameSession:
    world = load_world(world_root)
    save_dir = Path(save_root) / save_name
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / "save.json"
    if not save_path.exists():
        from app.core.store import write_json_atomic

        now = ""
        save = SaveData(
            meta={
                "world_id": world.meta.id,
                "save_name": save_name,
                "created_at": now,
                "next_event_id": 1,
            },
            clock="2026-07-14T08:00:00",
            player_scene="main_street",
            narrative_preset=world.presets,
        )
        write_json_atomic(save_path, save.model_dump())
    return GameSession(world, save_dir)


def open_session(world_root: str | Path, save_root: str | Path, save_name: str) -> GameSession:
    world = load_world(world_root)
    save_dir = Path(save_root) / save_name
    if not (save_dir / "save.json").exists():
        raise FileNotFoundError(f"save not found: {save_dir}")
    return GameSession(world, save_dir)