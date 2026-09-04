from __future__ import annotations

from app.core.store import new_id
from app.ledger.queries import Ledger
from app.ledger.save import Hook


def scan_hooks(ledger: Ledger, narrative_event: dict) -> list[Hook]:
    """Very small hook scanner.

    The full M14 implementation is LLM-assisted; this rule-based version
    catches common promise patterns so the MVP has something testable.
    """
    body = narrative_event.get("body", "")
    found: list[Hook] = []
    for marker in ["明天", "改天", "下次", "答应", "帮你", "等我"]:
        if marker in body:
            hook = Hook(
                id=new_id("hk"),
                text=body[:80],
                level="npc",
                status="open",
                opened_at=narrative_event.get("at", ""),
                due=None,
                related=narrative_event.get("participants", [])[:3],
            )
            ledger.save.hooks.append(hook)
            found.append(hook)
            break
    return found