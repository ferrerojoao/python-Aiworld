from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def scene_description(scene_id: str, world: WorldContent, ledger: Ledger) -> str:
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    if scene is None:
        return "这里看起来是个还没登记的地方。"
    present = ledger.present_at(scene_id)
    names = []
    for subject in present:
        npc = world.npcs.get(subject)
        names.append(npc.name if npc else subject)
    suffix = f"在场：{('、'.join(names))}。" if names else "四下无人。"
    return f"这里是{scene.name}。{scene.perceivable}{suffix}"