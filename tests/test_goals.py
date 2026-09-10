from __future__ import annotations

from app.core.workorder import build_work_order
from app.ledger.goals import abandon_goal, add_goal, complete_goals
from app.ledger.save import Goal


def _ev():
    return {"at": "2026-07-14T08:00:00", "participants": ["player", "朱明"]}


def test_goal_add_abandon_complete(session):
    goal = add_goal(
        session.ledger,
        text="查明朱明打架的真相",
        kind="big",
        npc_id="朱明",
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
    active goals with their tags; no active goals → no block. NPC goals show
    their owner and get the two-advance-mode discipline."""
    add_goal(session.ledger, text="查明朱明打架的真相", kind="big", npc_id="朱明")
    add_goal(session.ledger, text="让主角答应下周跟她一起去接货", kind="small", subject="王蓉", npc_id="王蓉")

    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "剧情目标（玩家设立的方向）：" in order
    assert "- [大目标/主线·玩家] 查明朱明打架的真相（关联：朱明）" in order
    assert "- [小目标/支线·王蓉] 让主角答应下周跟她一起去接货（关联：王蓉）" in order
    assert "引导纪律" in order
    # NPC 目标的两形态推进纪律。
    assert "NPC 在场 → 让她自然提及" in order
    assert "NPC 不在场 → 安排她主动来找玩家" in order

    # 位置：金科玉律之后（✓），但 2026-09-10 重排后剧情目标在玩家资料之后、
    # 输出格式之前（尾部近因区——写作引导贴近动笔位置）。
    c_pos = order.index("金科玉律")
    g_pos = order.index("剧情目标")
    d_pos = order.index("玩家资料：")
    k_pos = order.index("输出必须是 JSON 对象")
    assert c_pos < d_pos < g_pos < k_pos


def test_goals_never_reach_actor_slice(session):
    add_goal(session.ledger, text="查明朱明打架的真相", kind="big")
    # 朱明在场（seed 定位事件）以保证 character snapshot 正常。
    session.ledger.append(
        {
            "id": session.ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T08:00:00",
            "location": "网吧",
            "participants": ["朱明"],
            "known_by": None,
            "body": "朱明在网吧。",
            "summary": "朱明在网吧。",
            "player_input": None,
            "source": "seed",
        }
    )
    actor = build_work_order("actor_朱明", session.world, session.ledger, "网吧")
    assert "查明朱明打架的真相" not in actor
    assert "剧情目标" not in actor


def test_audit_prompt_guards_advance_vs_complete(session):
    """审计工作单须：列出活动目标、NPC 目标准确归属、且写明
    "推进不算完成"防误判（王蓉提接货 ≠ 目标完成）。"""
    from app.core.workorder import build_audit_work_order

    add_goal(session.ledger, text="让主角答应下周跟她一起去接货", kind="small", subject="王蓉", npc_id="王蓉")
    order = build_audit_work_order(session.world, session.ledger, "主街")

    assert "活动目标：" in order
    assert "让主角答应下周跟她一起去接货" in order  # 目标全文在活动目标行
    assert "该目标 NPC 提及/推进目标" in order  # 推进≠完成防误判
    assert "推进不算完成" in order
