from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def scene_description(scene_id: str, world: WorldContent, ledger: Ledger) -> str:
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present = ledger.present_at(scene_id)
    suffix = f"在场：{('、'.join(present))}。" if present else "四下无人。"
    if scene is not None:
        return f"这里是{scene.id}。{scene.perceivable}{suffix}"
    return f"这里是{scene_id}，一处还没登记的地方。{suffix}"
