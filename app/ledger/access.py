from __future__ import annotations

from .queries import Ledger


def rejudge_public(ledger: Ledger, event_id: str) -> None:
    """Change a private event back to public by clearing its access override."""
    if event_id in ledger.by_id:
        ledger.save.access_overrides[event_id] = None


def apply_access_override(ledger: Ledger, event_id: str, known_by: list[str] | None) -> None:
    """改判执行：名单由导演/玩家直接给定落账（只改访问层，正文不动）。

    设计口径（2026-09-09 翻案）：不做检索式起草——改判是补救行为，事件
    公开期间可能在剧情上传给无数人，逻辑上不可能全部收回；玩家选择改判
    即接受"已传开者收不回"的逻辑代价（以更大的叙事理由压过它）。
    """
    ledger.save.access_overrides[event_id] = known_by
    # 改判同步 by_knower：知情名单即召回名单（增删成员都要反映到索引）。
    ledger.reindex_knowers(event_id)
    ledger.persist_save()