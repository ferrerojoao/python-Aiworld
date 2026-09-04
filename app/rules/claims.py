from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import WorldContent


def apply_claim(text: str, world: WorldContent, ledger: Ledger) -> dict | None:
    """Apply a simple player world-state claim (M2).

    MVP handles '<NPC>在<place>' patterns only.
    The claim is recorded as a normal narrative event, same as any other event.
    """
    for npc_id, npc in world.npcs.items():
        if npc.name in text and "在" in text:
            for scene in world.scenes:
                if scene.name in text or scene.id in text or any(a in text for a in scene.aliases):
                    return {
                        "kind": "narrative",
                        "location": scene.id,
                        "participants": [npc_id],
                        "known_by": None,
                        "body": f"{npc.name}在{scene.name}。",
                        "source": "override",
                    }
    return None