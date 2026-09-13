"""World book triggering (concept-driven, L0 rules, zero LLM).

世界书条目 = {id, keywords, body} 的概念化背景。命中 = 关键词子串出现在
输入文本里（玩家本轮输入 or 已采纳正文）。命中集以 active_lore_ids 存在
save.json 上，由引擎两处更新：

1. 预结算阶段（turn 开始）：本轮玩家输入命中 → 追加（填充剩余名额）；
2. 采纳阶段（commit）：清空后按已采纳正文重建（上轮事实优先）。

装配（workorder）只读 active_lore_ids，按 id 取条目全文拼入提示词。此外还有两条
不经 active_lore_ids 的通道（2026-09-13）：

- **常驻** ``always_on``：每轮必注入，不占关键词名额；
- **归属** ``subject``：归属角色**在场**即注入（``subject_hit_ids``），也不占关键词名额。
"""

from __future__ import annotations

from app.world.models import WorldContent

LORE_ACTIVE_CAP = 5
LORE_SUBJECT_CAP = 2  # 每个归属角色最多注入 2 条（2026-09-13 用户拍板）


def hit_entry_ids(world: WorldContent, text: str) -> list[str]:
    """Lore entry ids whose keywords appear in ``text``, in list order."""
    hits: list[str] = []
    if not text:
        return hits
    for entry in world.lorebook:
        if any(kw and kw in text for kw in entry.keywords):
            hits.append(entry.id)
    return hits


def subject_hit_ids(
    world: WorldContent,
    present_ids: list[str],
    cap: int = LORE_SUBJECT_CAP,
) -> list[str]:
    """归属条目：``subject`` 落在 ``present_ids`` 里 → 注入（每人最多 ``cap`` 条）。

    与关键词触发的本质区别：**不看正文有没有点到名，只看人在不在场**。
    角色真的登场时（哪怕正文全程只叫他"老头"）他的深入设定也进提示词；
    被谈论而本人不在场时，仍要靠关键词命中（那是世界书的原职责）。

    名额与关键词通道独立（不占 ``LORE_ACTIVE_CAP``）；超出每人上限的按录入
    顺序截断——重要的条目往上放。
    """
    if not present_ids:
        return []
    present = set(present_ids)
    used: dict[str, int] = {}
    hits: list[str] = []
    for entry in world.lorebook:
        subject = (entry.subject or "").strip()
        if not subject or subject not in present:
            continue
        if used.get(subject, 0) >= cap:
            continue
        used[subject] = used.get(subject, 0) + 1
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
