from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def scene_description(scene_id: str, world: WorldContent, ledger: Ledger) -> str:
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present = ledger.present_at(scene_id)
    names = []
    for subject in present:
        npc = world.npcs.get(subject)
        names.append(npc.name if npc else subject)
    suffix = f"在场：{('、'.join(names))}。" if names else "四下无人。"
    if scene is not None:
        return f"这里是{scene.name}。{scene.perceivable}{suffix}"
    if ledger.save.scene_name:
        return f"这里是{ledger.save.scene_name}，一处还没登记的地方。{suffix}"
    return f"这里看起来是个还没登记的地方。{suffix}"