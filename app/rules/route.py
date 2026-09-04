from __future__ import annotations

from app.world.models import WorldContent


def classify_input(text: str, world: WorldContent) -> str:
    """L0 rule-based routing; fallback is chat_act.

    Returns one of: move, jump, claim, query, chat_act.
    """
    text = text.strip()
    if text.startswith(("去", "回", "走到", "前往")):
        return "move"
    if "等到" in text or "跳到" in text or "第二天" in text:
        return "jump"
    if "在哪" in text or "谁在" in text or "什么时辰" in text:
        return "query"
    for npc in world.npcs.values():
        if f"{npc.name}在" in text:
            return "claim"
    return "chat_act"