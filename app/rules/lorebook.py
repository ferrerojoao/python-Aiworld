"""World book triggering (concept-driven, L0 rules, zero LLM).

世界书条目 = {id, keywords, body} 的概念化背景。命中 = 关键词子串出现在
输入文本里（玩家本轮输入 or 已采纳正文）。命中集以 active_lore_ids 存在
save.json 上，由引擎两处更新：

1. 预结算阶段（turn 开始）：本轮玩家输入命中 → 追加（填充剩余名额）；
2. 采纳阶段（commit）：清空后按已采纳正文重建（上轮事实优先）。

装配（workorder）只读 active_lore_ids，按 id 取条目全文拼入提示词。
"""

from __future__ import annotations

from app.world.models import WorldContent

LORE_ACTIVE_CAP = 5


def hit_entry_ids(world: WorldContent, text: str) -> list[str]:
    """Lore entry ids whose keywords appear in ``text``, in list order."""
    hits: list[str] = []
    if not text:
        return hits
    for entry in world.lorebook:
        if any(kw and kw in text for kw in entry.keywords):
            hits.append(entry.id)
    return hits


def merge_active_lore(current: list[str], hits: list[str], cap: int = LORE_ACTIVE_CAP) -> list[str]:
    """Append hits to the active list, deduped, capped.

    Existing entries (previous prose facts) keep priority; new hits fill the
    remaining slots. Returns the merged list (does not mutate in place).
    """
    merged = list(current)
    for entry_id in hits:
        if len(merged) >= cap:
            break
        if entry_id not in merged:
            merged.append(entry_id)
    return merged[:cap]


def rebuild_active_lore(world: WorldContent, prose: str, cap: int = LORE_ACTIVE_CAP) -> list[str]:
    """Rebuild the active list from an adopted prose (采纳阶段：清空重建)."""
    return hit_entry_ids(world, prose)[:cap]
