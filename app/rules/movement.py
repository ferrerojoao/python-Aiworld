from __future__ import annotations

from collections import deque

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def resolve_destination(text: str, world: WorldContent, ledger: Ledger) -> str | None:
    """Resolve a destination from player text.

    Three routes return None when unresolved, so the audit inference from the
    adopted prose owns the final location:
    1. direct scene alias -> scene id
    2. 'find NPC' -> NPC schedule/last location (None when no fact)
    3. unknown named place -> None (audit may register/reuse from prose)
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
            # 无最近位置事实时不猜测，交给审计按正文结算
            return None

    # 未命中的地点不硬编码回主街；正文语义由采纳时审计推断。
    return None


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