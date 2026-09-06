from __future__ import annotations

from app.world.models import WorldContent


def classify_input(text: str, world: WorldContent) -> str:
    """L0 rule-based routing; fallback is chat_act.

    Returns one of: move, jump, chat_act.
    """
    text = text.strip()
    if text.startswith(("去", "回", "走到", "前往", "我去", "我想去")):
        return "move"
    if "等到" in text or "跳到" in text or "第二天" in text:
        return "jump"
    return "chat_act"