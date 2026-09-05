from __future__ import annotations

from app.ledger.hooks import add_hooks, close_hooks


def _ev(text: str = "朱明说改天一起吃饭。") -> dict:
    return {"at": "2026-07-14T09:00:00", "participants": ["player", "npc_zhuming"], "body": text}


def test_add_hooks_from_llm_out(session):
    added = add_hooks(
        session.ledger,
        ["答应明天帮王蓉修电脑", "说好一起去鱼市"],
        event=_ev(),
        limit=20,
    )
    assert len(added) == 2
    assert all(h.status == "open" for h in added)
    assert added[0].text == "答应明天帮王蓉修电脑"
    assert added[0].related == ["player", "npc_zhuming"]
    # blank texts are skipped
    assert add_hooks(session.ledger, ["", "  "], event=_ev(), limit=20) == []


def test_hook_limit_expires_oldest_first(session):
    for i in range(5):
        add_hooks(session.ledger, [f"钩子{i}"], event=_ev(), limit=3)
    hooks = session.ledger.save.hooks
    assert len(hooks) == 5
    expired = [h for h in hooks if h.status == "expired"]
    assert [h.text for h in expired] == ["钩子0", "钩子1"]
    opened = [h for h in hooks if h.status == "open"]
    assert [h.text for h in opened] == ["钩子2", "钩子3", "钩子4"]
    # expired hooks exit the open set entirely
    assert all(h.closed_at for h in expired)


def test_close_hooks_fulfils_by_id(session):
    added = add_hooks(session.ledger, ["明天还你钱"], event=_ev(), limit=20)
    closed = close_hooks(session.ledger, [added[0].id])
    assert len(closed) == 1
    assert session.ledger.save.hooks[0].status == "closed"
    assert session.ledger.save.hooks[0].closed_at
    # fulfilling again is a no-op
    assert close_hooks(session.ledger, [added[0].id]) == []
    # unknown ids are ignored
    assert close_hooks(session.ledger, ["hk_missing"]) == []