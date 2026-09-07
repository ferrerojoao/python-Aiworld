from __future__ import annotations

from app.core.workorder import build_work_order
from app.ledger.goals import abandon_goal, add_goal, complete_goals
from app.ledger.save import Goal


def _ev():
    return {"at": "2026-07-14T08:00:00", "participants": ["player", "npc_zhuming"]}


def test_goal_add_abandon_complete(session):
    goal = add_goal(
        session.ledger,
        text="查明朱明打架的真相",
        kind="big",
        npc_id="npc_zhuming",
    )
    assert goal.status == "active"
    assert goal.kind == "big"

    child = add_goal(
        session.ledger,
        text="帮朱明包扎伤口",
        kind="small",
        big_goal_id=goal.id,
    )
    assert child.big_goal_id == goal.id

    completed = complete_goals(session.ledger, [goal.id])
    assert len(completed) == 1
    assert goal.status == "done"
    assert goal.done_at
    # 已完成的不能再判
    assert complete_goals(session.ledger, [goal.id]) == []

    abandoned = abandon_goal(session.ledger, child.id)
    assert abandoned is not None
    assert child.status == "abandoned"
    assert abandon_goal(session.ledger, child.id) is None


def test_goal_cap(session):
    for i in range(6):
        add_goal(session.ledger, text=f"目标{i}", kind="small")
    import pytest

    with pytest.raises(ValueError):
        add_goal(session.ledger, text="超限", kind="small")


def test_goals_block_after_preset(session):
    """Goal block appears in the writer order AFTER the preset block and lists
    active goals with their tags; no active goals → no block."""
    add_goal(session.ledger, text="查明朱明打架的真相", kind="big", npc_id="npc_zhuming")
    add_goal(session.ledger, text="帮王蓉修好渔船", kind="small", npc_id="npc_wangrong")

    order = build_work_order("writer", session.world, session.ledger, "net_bar")
    assert "剧情目标（玩家设立的方向）：" in order
    assert "- [大目标/主线] 查明朱明打架的真相（关联：朱明）" in order
    assert "- [小目标/支线] 帮王蓉修好渔船（关联：王蓉）" in order
    assert "引导纪律" in order

    # 位置：金科玉律之后、玩家资料之前（C → G → D；预设段为空时同样成立）。
    c_pos = order.index("金科玉律")
    g_pos = order.index("剧情目标")
    d_pos = order.index("玩家资料：")
    assert c_pos < g_pos < d_pos


def test_goals_never_reach_actor_slice(session):
    add_goal(session.ledger, text="查明朱明打架的真相", kind="big")
    # 朱明在场（seed 定位事件）以保证 character snapshot 正常。
    session.ledger.append(
        {
            "id": session.ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T08:00:00",
            "location": "net_bar",
            "participants": ["npc_zhuming"],
            "known_by": None,
            "body": "朱明在网吧。",
            "summary": "朱明在网吧。",
            "player_input": None,
            "source": "seed",
        }
    )
    actor = build_work_order("actor_npc_zhuming", session.world, session.ledger, "net_bar")
    assert "查明朱明打架的真相" not in actor
    assert "剧情目标" not in actor
