from __future__ import annotations

import datetime as dt

from app.core.store import new_id
from app.ledger.queries import Ledger
from app.ledger.save import Hook

# 引擎侧钩子上限：达到上限后按产生顺序（最旧先）淘汰。
# 上限由 Settings.hook_limit 注入（默认 20），确保账本与编剧工作单都不膨胀。
DEFAULT_HOOK_LIMIT = 20


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def add_hooks(
    ledger: Ledger,
    texts: list[str],
    *,
    event: dict,
    limit: int = DEFAULT_HOOK_LIMIT,
) -> list[Hook]:
    """Register hook entries from the audit LLM's extraction.

    Each text is a promise / unresolved matter in the player's words. When
    the open count reaches the limit, the oldest open hook (by open order)
    is expired to make room — no due-date parsing is ever attempted.
    """
    participants = (event.get("participants") or [])[:3]
    created: list[Hook] = []
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        _expire_oldest(ledger, limit)
        hook = Hook(
            id=new_id("hk"),
            text=text[:120],
            level="npc",
            status="open",
            opened_at=event.get("at") or _now(),
            closed_at=None,
            due=None,
            related=participants,
        )
        ledger.save.hooks.append(hook)
        created.append(hook)
    return created


def close_hooks(ledger: Ledger, hook_ids: list[str]) -> list[Hook]:
    """Fulfil open hooks by id (the audit LLM decided the current event
    fulfilled them)."""
    closed: list[Hook] = []
    now = _now()
    for hook in ledger.save.hooks:
        if hook.id in hook_ids and hook.status == "open":
            hook.status = "closed"
            hook.closed_at = now
            closed.append(hook)
    return closed


def _expire_oldest(ledger: Ledger, limit: int) -> None:
    """Expire the oldest open hook (list order = production order)."""
    while True:
        open_hooks = [h for h in ledger.save.hooks if h.status == "open"]
        if len(open_hooks) < limit or not open_hooks:
            return
        oldest = next(h for h in ledger.save.hooks if h.status == "open")
        oldest.status = "expired"
        oldest.closed_at = _now()