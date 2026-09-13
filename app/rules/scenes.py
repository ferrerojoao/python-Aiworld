from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def scene_description(scene_id: str, world: WorldContent, ledger: Ledger) -> str:
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    # 玩家视角：这里列的是"还有谁在"，主角自己不必出现在自己的视野里
    #（2026-09-13 主角入人物表后 present_at 会带上他，故显式剔除）。
    present = [pid for pid in ledger.present_at(scene_id) if pid != world.player_name()]
    suffix = f"在场：{('、'.join(present))}。" if present else "四下无人。"
    if scene is not None:
        return f"这里是{scene.id}。{scene.perceivable}{suffix}"
    return f"这里是{scene_id}，一处还没登记的地方。{suffix}"
