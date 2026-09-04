from __future__ import annotations

from collections import deque

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def resolve_destination(text: str, world: WorldContent, ledger: Ledger) -> str | None:
    """Resolve a destination from player text.

    Four routes:
    1. direct scene alias
    2. 'find NPC' -> NPC schedule/last location
    3. 'last/that place' -> player's last location (not tracked yet; fallback main_street)
    4. unknown named place -> register ad-hoc (not implemented in MVP, return main_street)
    """
    text = text.strip()
    alias_map = {}
    for scene in world.scenes:
        for alias in [scene.name, scene.id, *scene.aliases]:
            alias_map[alias.lower()] = scene.id

    for alias, scene_id in alias_map.items():
        if alias and alias in text.lower():
            return scene_id

    for npc_id, npc in world.npcs.items():
        if npc.name in text:
            fact = ledger.where_is(npc_id)
            if fact and fact.get("location"):
                return fact["location"]
            # Fall back to schedule cue.
            if npc.normal_schedule and "网吧" in npc.normal_schedule:
                return "net_bar"
            if npc.normal_schedule and "修车铺" in npc.normal_schedule:
                return "main_street"
            return "main_street"

    if "昨天" in text or "刚才" in text or "那个" in text:
        return "main_street"

    # New ad-hoc locations are not implemented yet; land on main street.
    return "main_street"


def travel_minutes(from_scene: str | None, to_scene: str, world: WorldContent) -> int:
    if from_scene == to_scene:
        return 0

    adjacency: dict[str, list[str]] = {s.id: list(s.adjacent) for s in world.scenes}

    start = from_scene or "main_street"
    if start not in adjacency or to_scene not in adjacency:
        return world.meta.default_durations.get("move_per_edge_min", 10)

    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        current, distance = queue.popleft()
        if current == to_scene:
            return distance * world.meta.default_durations.get("move_per_edge_min", 10)
        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, distance + 1))
    return world.meta.default_durations.get("move_per_edge_min", 10)