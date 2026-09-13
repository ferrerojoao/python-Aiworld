from __future__ import annotations

from pathlib import Path

from app.core.store import write_json_atomic
from app.ledger.queries import Ledger
from app.ledger.save import SaveData
from app.runtime.transaction import CandidateStore
from app.world.loader import load_world
from app.world.models import WorldContent


class GameSession:
    """一个世界 = 一个存档（单一真相源，2026-09-13 改版）。

    世界资产（world.json / npcs/ / scenes.json / lorebook.json / axes.json）
    与运行态（save.json / events.jsonl / candidates/）同住 ``content/<world>/``
    一层目录——正在玩的状态就是唯一真相，没有模板/副本之分。
    ``world_dir`` 与 ``save_dir`` 是同一目录的两个名字，保留两个属性名是
    为了调用方（工作台读写世界资产 / 账本读写运行态）语义清晰。
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.world_dir = self.root
        self.save_dir = self.root
        self.world: WorldContent = load_world(self.world_dir)
        self.ledger = Ledger(self.world, self.save_dir)
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

        return scene_description(self.ledger.current_scene(), self.world, self.ledger)


DEFAULT_START_TIME = "2026-07-14T08:00:00"


def world_start_time(world: WorldContent) -> str:
    """世界钟起点：内容包 start_time 优先，空则回退引擎默认。"""
    return world.meta.start_time or DEFAULT_START_TIME


def write_opening_event(session: GameSession) -> bool:
    """Write the content pack's opening prose as the first ledger event.

    The opening belongs to the world assets: it becomes event #1 of a fresh
    save and must survive resets (reset re-creates it after wiping events).

    开场事件带 location（2026-09-13）：主角是人物表里的普通一员，其"在场"
    由最近一条定位事件推导——开场就是主角在本世界的第一条位置事实，否则
    第一轮工作单的在场名单里没有主角。
    Returns True when an opening was written.
    """
    world = session.world
    if not world.meta.opening:
        return False
    ledger = session.ledger
    scene = ledger.current_scene() or None
    event = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": ledger.save.clock or world_start_time(world),
        "location": scene,
        "participants": [world.player_name()],
        "known_by": None,
        "body": world.meta.opening,
        "summary": "开场",
        "player_input": None,
        "source": "opening",
    }
    ledger.append(event)
    ledger.persist_save()
    return True


def create_session(world_root: str | Path, save_name: str = "main") -> GameSession:
    """Create a playthrough in the world directory itself (世界=存档，1:1).

    save.json / events.jsonl 直接落在 ``content/<world>/``；世界资产不再复制
    副本。已有进行中的存档则拒绝（接着玩请走 ``open_session``）。
    """
    root = Path(world_root)
    if (root / "save.json").exists():
        raise FileExistsError(f"world already has a save: {root}")
    world = load_world(root)
    save = SaveData(
        meta={
            "world_id": world.meta.id,
            "save_name": save_name,
            "created_at": "",
            "next_event_id": 1,
        },
        clock=world_start_time(world),
        player_scene=world.start_scene_id(),
        narrative_preset=world.presets,
    )
    write_json_atomic(root / "save.json", save.model_dump())
    session = GameSession(root)
    write_opening_event(session)
    return session


def open_session(world_root: str | Path) -> GameSession:
    root = Path(world_root)
    if not (root / "save.json").exists():
        raise FileNotFoundError(f"save not found: {root}")
    return GameSession(root)