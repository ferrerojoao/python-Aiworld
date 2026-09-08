from __future__ import annotations

import shutil
from pathlib import Path

from app.core.store import write_json_atomic
from app.ledger.queries import Ledger
from app.ledger.save import SaveData
from app.runtime.transaction import CandidateStore
from app.world.loader import load_world
from app.world.models import WorldContent


def _asset_files(template_root: Path) -> list[Path]:
    """The template files that seed a save's world instance (excl. saves/)."""
    return [p for p in template_root.rglob("*") if p.is_file() and "saves" not in p.parts]


class GameSession:
    def __init__(self, world_dir: str | Path, save_dir: str | Path):
        self.world_dir = Path(world_dir)
        self.save_dir = Path(save_dir)
        self.world: WorldContent = load_world(self.world_dir)
        self.ledger = Ledger(self.world, save_dir)
        self.candidates = CandidateStore(self.save_dir / "candidates")
        self.director_history: list[dict] = []
        self.debug_trace: list[dict] = []

    @property
    def sid(self) -> str:
        return f"{self.ledger.save.meta.world_id}:{self.ledger.save.meta.save_name}"

    def reload_world(self) -> None:
        """Reload the world instance from disk (after editing it) and keep the
        ledger's reference in sync."""
        self.world = load_world(self.world_dir)
        self.ledger.world = self.world

    def scene_description(self) -> str:
        from app.rules.scenes import scene_description

        return scene_description(self.ledger.save.player_scene, self.world, self.ledger)


DEFAULT_START_TIME = "2026-07-14T08:00:00"


def world_start_time(world: WorldContent) -> str:
    """世界钟起点：内容包 start_time 优先，空则回退引擎默认。"""
    return world.meta.start_time or DEFAULT_START_TIME


def write_opening_event(session: GameSession) -> bool:
    """Write the content pack's opening prose as the first ledger event.

    The opening belongs to the world assets: it becomes event #1 of a fresh
    save and must survive resets (reset re-creates it after wiping events).
    Returns True when an opening was written.
    """
    world = session.world
    if not world.meta.opening:
        return False
    ledger = session.ledger
    event = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": ledger.save.clock or world_start_time(world),
        "location": None,
        "participants": ["player"],
        "known_by": None,
        "body": world.meta.opening,
        "summary": "开场",
        "player_input": None,
        "source": "opening",
    }
    ledger.append(event)
    ledger.persist_save()
    return True


def copy_world_instance(template_root: str | Path, instance_dir: str | Path) -> None:
    """Seed a save's world instance from the content-pack template (deep copy).

    The instance is fully writable; the template stays read-only for creating
    new saves (design C: 世界 = 存档内实例，资产 = 模板).
    """
    template_root = Path(template_root)
    instance_dir = Path(instance_dir)
    for src in _asset_files(template_root):
        target = instance_dir / src.relative_to(template_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def create_session(template_root: str | Path, save_root: str | Path, save_name: str) -> GameSession:
    """Create a save: the world instance is a deep copy of the template."""
    world = load_world(template_root)
    save_dir = Path(save_root) / save_name
    save_dir.mkdir(parents=True, exist_ok=True)
    instance_dir = save_dir / "world"
    created = False
    if not (save_dir / "save.json").exists():
        created = True
        save = SaveData(
            meta={
                "world_id": world.meta.id,
                "save_name": save_name,
                "created_at": "",
                "next_event_id": 1,
            },
            clock=world_start_time(world),
            player_scene="main_street",
            narrative_preset=world.presets,
        )
        write_json_atomic(save_dir / "save.json", save.model_dump())
        copy_world_instance(template_root, instance_dir)
    session = GameSession(instance_dir, save_dir)
    if created:
        write_opening_event(session)
    return session


def open_session(save_root: str | Path, save_name: str) -> GameSession:
    save_dir = Path(save_root) / save_name
    if not (save_dir / "save.json").exists():
        raise FileNotFoundError(f"save not found: {save_dir}")
    instance_dir = save_dir / "world"
    if not instance_dir.is_dir():
        raise FileNotFoundError(f"world instance not found: {instance_dir}")
    return GameSession(instance_dir, save_dir)


def rebuild_world_instance(session: GameSession, template_root: str | Path) -> None:
    """Reset semantics: rebuild the writable instance from the template."""
    template_root = Path(template_root)
    shutil.rmtree(session.world_dir, ignore_errors=True)
    session.world_dir.mkdir(parents=True, exist_ok=True)
    copy_world_instance(template_root, session.world_dir)
    session.reload_world()