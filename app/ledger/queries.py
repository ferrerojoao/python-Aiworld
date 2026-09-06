from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.store import append_event, read_events, read_json, write_json_atomic
from app.ledger.save import SaveData
from app.world.models import WorldContent


class Ledger:
    """Event stream + save.json in-memory facade.

    The event stream is the source of truth; save.json holds engine runtime
    state, player preset overrides and entity runtime patches. Derived
    indexes (last_location / by_participant) are maintained incrementally on
    append and rebuilt fully on load/reset — they are accelerations, never a
    second source of truth (design: 查询派生，索引加速).
    """

    def __init__(self, world: WorldContent, save_dir: str | Path):
        self.world = world
        self.save_dir = Path(save_dir)
        self.events_path = self.save_dir / "events.jsonl"
        self.save_path = self.save_dir / "save.json"

        raw_save = read_json(self.save_path, None)
        if raw_save is None:
            save_name = self.save_dir.name
            self.save = SaveData(
                meta={
                    "world_id": world.meta.id,
                    "save_name": save_name,
                    "created_at": "",
                    "next_event_id": 1,
                }
            )
        else:
            self.save = SaveData.model_validate(raw_save)

        self.events: list[dict[str, Any]] = read_events(self.events_path)
        self._rebuild_indexes()

    # ------------------------------------------------------------------
    # Indexing (derived, rebuilt on load/reset, updated on append)
    # ------------------------------------------------------------------
    def _rebuild_indexes(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_location: dict[str, list[dict[str, Any]]] = {}
        self.narratives: list[dict[str, Any]] = []
        # 小抄①：每个 NPC（含 player）当前由其最近一条定位事件确定的位置
        self.last_location: dict[str, dict[str, Any]] = {}
        # 小抄②：每个人参与过哪些事件（experiences 热路径）
        self.by_participant: dict[str, list[dict[str, Any]]] = {}

        for event in self.events:
            self._index(event)

    def _index(self, event: dict[str, Any]) -> None:
        """Update in-memory indexes for one event (append and rebuild both)."""
        self.by_id[event["id"]] = event
        if event["kind"] != "narrative":
            return
        self.narratives.append(event)
        loc = event.get("location")
        if loc:
            self.by_location.setdefault(loc, []).append(event)
        # 小抄①：带位置的新事件，确定了其参与者此刻的位置
        if loc:
            for pid in event.get("participants", []):
                self.last_location[pid] = event
        # 小抄②：参与者索引
        for pid in event.get("participants", []):
            self.by_participant.setdefault(pid, []).append(event)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def allocate_event_id(self) -> str:
        event_id = f"ev_{self.save.meta.next_event_id:05d}"
        self.save.meta.next_event_id += 1
        return event_id

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        append_event(self.events_path, event)
        self._index(event)

    def persist_save(self) -> None:
        write_json_atomic(self.save_path, self.save.model_dump())

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def where_is(self, entity: str) -> dict[str, Any] | None:
        return self.last_location.get(entity)

    def present_at(self, scene: str) -> list[str]:
        result = []
        for subject, event in self.last_location.items():
            if subject not in self.world.npcs:
                continue  # player 等非 NPC 不算在场者
            entity = self.save.entities.get(subject)
            if entity and entity.lifecycle == "retired":
                continue  # 退场者停止参与在场推导与主动调度（REQ 〇章）
            if event.get("location") == scene:
                result.append(subject)
        return sorted(result)

    def visible_to(self, viewer: str) -> list[dict[str, Any]]:
        """Narrative events visible to a viewer.

        Public events are visible to everyone; private events require the
        viewer to be in known_by. Access overrides in save.json take
        precedence over the original event field.
        """
        result = []
        for event in self.narratives:
            known_by = self.save.access_overrides.get(event["id"], event.get("known_by"))
            if known_by is None or viewer in known_by:
                result.append(event)
        return result

    def experiences(self, entity: str, viewer: str) -> list[str]:
        """Memory entries for an entity from the viewer's view.

        Uses the by_participant index to reach only events relevant to the
        pair, then applies the known_by visibility filter per event.
        """
        limit = self.world.meta.default_durations.get("memory_limit", 50)
        lines: list[str] = []
        seen: set[str] = set()
        for ev in self.by_participant.get(entity, []) + self.by_participant.get(viewer, []):
            ev_id = ev["id"]
            if ev_id in seen:
                continue
            seen.add(ev_id)
            known_by = self.save.access_overrides.get(ev_id, ev.get("known_by"))
            if known_by is not None and viewer not in known_by:
                continue
            participants = ev.get("participants", [])
            if entity not in participants and viewer not in participants:
                continue
            summary = ev.get("summary") or (ev.get("body") or "")[:40]
            lines.append(f"{ev.get('at', '')} {summary}")
        return lines[-limit:] if lines else []

    def lore_candidates(self, scene_id: str, npc_ids: list[str]) -> list[dict[str, Any]]:
        tags = set()
        for scene in self.world.scenes:
            if scene.id == scene_id:
                tags.update(scene.tags)
        tags.update(npc_ids)
        candidates = []
        for entry in self.world.lorebook:
            if tags.intersection(entry.tags):
                candidates.append({"id": entry.id, "summary": entry.summary})
        return candidates[:12]