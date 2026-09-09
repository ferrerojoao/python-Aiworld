from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from app.core.store import append_event, read_events, read_json, write_json_atomic
from app.ledger.save import SaveData
from app.world.models import WorldContent


class Ledger:
    """Event stream + save.json in-memory facade.

    The event stream is the source of truth; save.json holds engine runtime
    state, player preset overrides and entity runtime patches. Derived
    indexes (last_location / by_participant / by_knower) are maintained
    incrementally on append and rebuilt fully on load/reset — they are
    accelerations, never a second source of truth (design: 查询派生，索引加速).
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
        self._pending_events: list[dict[str, Any]] = []
        self._calibrate_event_counter()
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
        # 小抄③：私密事件按 known_by 成员挂账（知情即可召回）；
        # _knower_members 记录每个事件当前挂了哪些成员，改判时同步增删。
        self.by_knower: dict[str, list[dict[str, Any]]] = {}
        self._knower_members: dict[str, set[str]] = {}
        # 索引命中率统计（展示用，量测"小抄"是否在起作用）
        self.cache_stats: dict[str, int] = {"hits": 0, "misses": 0}

        for event in self.events:
            self._index(event)

    def _calibrate_event_counter(self) -> None:
        """事件流水是权威：加载时按流水里实际用过的最大号校准计数器。

        采纳落盘是"先写流水、再写存档"背靠背两笔——若崩在两笔之间，
        存档里的 next_event_id 是旧值，直接沿用会复用事件号（同号互相
        覆盖、改判/目标等按号引用全部错位）。校准让该窗口无害。
        """
        max_seq = 0
        for event in self.events:
            match = re.fullmatch(r"ev_(\d+)", str(event.get("id", "")))
            if match:
                max_seq = max(max_seq, int(match.group(1)))
        if max_seq + 1 > self.save.meta.next_event_id:
            self.save.meta.next_event_id = max_seq + 1

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
        # 小抄③：知情名单索引（私密事件）
        self._reindex_knowers(event)

    def _reindex_knowers(self, event: dict[str, Any]) -> None:
        """(Re)index one event's by_knower entries from its *effective* known_by.

        Access overrides take precedence over the raw field (same rule as
        visible_to). Called on append and again whenever an override changes,
        so a rejudge that adds/removes a knower keeps recall aligned with
        visibility: 谁能引用，谁就能被召回.
        """
        ev_id = event["id"]
        for member in self._knower_members.pop(ev_id, set()):
            entries = self.by_knower.get(member)
            if entries:
                remaining = [e for e in entries if e["id"] != ev_id]
                if remaining:
                    self.by_knower[member] = remaining
                else:
                    del self.by_knower[member]
        known_by = self.save.access_overrides.get(ev_id, event.get("known_by"))
        members: set[str] = set()
        if known_by is not None:
            for member in known_by:
                self.by_knower.setdefault(member, []).append(event)
                members.add(member)
        self._knower_members[ev_id] = members

    def reindex_knowers(self, event_id: str) -> None:
        """Public hook for access-control writes: rebuild by_knower for one event."""
        event = self.by_id.get(event_id)
        if event is not None and event["kind"] == "narrative":
            self._reindex_knowers(event)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def allocate_event_id(self) -> str:
        event_id = f"ev_{self.save.meta.next_event_id:05d}"
        self.save.meta.next_event_id += 1
        return event_id

    def append(self, event: dict[str, Any], *, flush: bool = True) -> None:
        """Add one event to the stream and indexes.

        flush=True (default) writes through to events.jsonl immediately.
        flush=False keeps the write in memory only — used by turn commit so
        all of a turn's events hit disk together with save.json (one commit
        unit); call flush_events() at the end of the commit.
        """
        self.events.append(event)
        self._index(event)
        if flush:
            append_event(self.events_path, event)
        else:
            self._pending_events.append(event)

    def flush_events(self) -> None:
        """Write deferred events to events.jsonl (commit tail)."""
        while self._pending_events:
            append_event(self.events_path, self._pending_events.pop(0))

    def persist_save(self) -> None:
        write_json_atomic(self.save_path, self.save.model_dump())

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def _probe(self, entity: str) -> dict[str, Any] | None:
        """Look up the location snapshot, counting index hits/misses."""
        event = self.last_location.get(entity)
        if event is not None:
            self.cache_stats["hits"] += 1
        else:
            self.cache_stats["misses"] += 1
        return event

    def where_is(self, entity: str) -> dict[str, Any] | None:
        return self._probe(entity)

    def present_at(self, scene: str) -> list[str]:
        result = []
        for subject in self.world.npcs.keys():
            event = self._probe(subject)
            if event is None:
                continue
            entity = self.save.entities.get(subject)
            if entity and entity.lifecycle == "retired":
                continue  # 退场者停止参与在场推导与主动调度（REQ 〇章）
            if event.get("location") == scene:
                result.append(subject)
        return sorted(result)

    def cache_stats_snapshot(self) -> dict[str, int]:
        return dict(self.cache_stats)

    def register_scene(self, scene_id: str, name: str) -> bool:
        """Register a reusable scene node in the save's world instance
        (M17 转正，仅玩家声明的可复用地点；一次性布景不注册)."""
        if any(s.id == scene_id for s in self.world.scenes):
            return False
        from app.core.store import write_json_atomic
        from app.world.models import Scene

        scene = Scene(
            id=scene_id,
            name=name or scene_id,
            aliases=[name or scene_id],
            perceivable="这里看起来是个还没仔细描述的地方。",
            open_hours="全天",
            adjacent=[self.save.player_scene],
        )
        self.world.scenes.append(scene)
        scenes_path = self.save_dir / "world" / "scenes.json"
        write_json_atomic(scenes_path, [s.model_dump() for s in self.world.scenes])
        return True

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

        Candidates come from two indexes — by_participant（亲历）and
        by_knower（known_by 名单内的知情，含改判扩名单者）— then the
        effective known_by visibility filter applies per event. Knower
        recall keeps experiences aligned with visible_to: a private event
        whose known_by lists someone who is not a participant (改判扩名单 /
        幕后得知) still reaches their memory slice.
        """
        limit = self.world.meta.default_durations.get("memory_limit", 50)
        lines: list[str] = []
        seen: set[str] = set()
        candidates = (
            self.by_participant.get(entity, [])
            + self.by_participant.get(viewer, [])
            + self.by_knower.get(entity, [])
            + self.by_knower.get(viewer, [])
        )
        for ev in candidates:
            ev_id = ev["id"]
            if ev_id in seen:
                continue
            seen.add(ev_id)
            known_by = self.save.access_overrides.get(ev_id, ev.get("known_by"))
            if known_by is not None and viewer not in known_by:
                continue
            participants = ev.get("participants", [])
            in_known = known_by is not None and (entity in known_by or viewer in known_by)
            if entity not in participants and viewer not in participants and not in_known:
                continue
            summary = ev.get("summary") or (ev.get("body") or "")[:40]
            lines.append(f"{ev.get('at', '')} {summary}")
        return lines[-limit:] if lines else []