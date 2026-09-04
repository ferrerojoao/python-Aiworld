from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.store import append_event, new_id, read_events, read_json, write_json_atomic
from app.ledger.save import SaveData
from app.world.models import WorldContent


class Ledger:
    """Event stream + save.json in-memory facade.

    The event stream is the source of truth; save.json holds engine runtime
    state, player preset overrides and entity runtime patches.
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
    # Indexing
    # ------------------------------------------------------------------
    def _rebuild_indexes(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_location: dict[str, list[dict[str, Any]]] = {}
        self.narratives: list[dict[str, Any]] = []

        for event in self.events:
            self.by_id[event["id"]] = event
            if event["kind"] == "narrative":
                self.narratives.append(event)
                loc = event.get("location")
                if loc:
                    self.by_location.setdefault(loc, []).append(event)

    # ------------------------------------------------------------------
    # Writes (only called from the transaction commit path)
    # ------------------------------------------------------------------
    def allocate_event_id(self) -> str:
        event_id = f"ev_{self.save.meta.next_event_id:05d}"
        self.save.meta.next_event_id += 1
        return event_id

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.by_id[event["id"]] = event
        append_event(self.events_path, event)
        if event["kind"] == "narrative":
            self.narratives.append(event)
            loc = event.get("location")
            if loc:
                self.by_location.setdefault(loc, []).append(event)

    def persist_save(self) -> None:
        write_json_atomic(self.save_path, self.save.model_dump())

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def _latest_location_event(self, entity: str) -> dict[str, Any] | None:
        """Find the latest event that establishes an entity's location.

        Only unified narrative events count (participants + location).
        """
        for event in reversed(self.events):
            location = event.get("location")
            if not location:
                continue
            if entity in event.get("participants", []):
                return event
        return None

    def where_is(self, entity: str) -> dict[str, Any] | None:
        event = self._latest_location_event(entity)
        if event is None:
            return None
        return event

    def present_at(self, scene: str) -> list[str]:
        result = []
        for subject in self.world.npcs.keys():
            event = self._latest_location_event(subject)
            if event and event.get("location") == scene:
                result.append(subject)
        return sorted(result)

    def visible_to(self, viewer: str, *, include_memo: bool = False) -> list[dict[str, Any]]:
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
        """Rule-assembled memory entries for an entity from the viewer's view."""
        lines: list[str] = []
        for ev in self.visible_to(viewer):
            participants = ev.get("participants", [])
            if entity not in participants and viewer not in participants:
                continue
            at = ev.get("at", "")
            loc = ev.get("location") or "某处"
            body = (ev.get("body") or "")[:60]
            lines.append(f"{at} {loc}，{body}")
        return lines[-self.world.meta.default_durations.get("memory_limit", 50) :] if lines else []

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