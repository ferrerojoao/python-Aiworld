from __future__ import annotations

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
        for alias in [scene.id, *scene.aliases]:  # 场景键 = 中文名
            alias_map[alias.lower()] = scene.id

    for alias, scene_id in alias_map.items():
        if alias and alias in text.lower():
            return scene_id

    for npc_id in world.npcs:
        if npc_id in text:  # NPC 键 = 中文名
            fact = ledger.where_is(npc_id)
            if fact and fact.get("location"):
                return fact["location"]
            # 无最近位置事实时不猜测，交给审计按正文结算
            return None

    # 未命中的地点不硬编码回主街；正文语义由采纳时审计推断。
    return None
