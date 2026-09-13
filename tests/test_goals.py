from __future__ import annotations

import pytest

from app.core.workorder import build_work_order
from app.ledger.goals import abandon_goal, add_goal, complete_goals


def _ev():
    return {"at": "2026-07-14T08:00:00", "participants": ["刘星", "朱明"]}


def test_goal_add_complete_cascades_and_abandon(session):
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
    # 父闭合 → 其下 active 子目标级联撤下（数据卫生，不落事件）
    assert child.status == "abandoned"
    assert child.done_at
    # 已完成的不能再判
    assert complete_goals(session.ledger, [goal.id]) == []

    lone = add_goal(session.ledger, text="学一手做饭", kind="small")
    abandoned = abandon_goal(session.ledger, lone.id)
    assert abandoned is not None
    assert lone.status == "abandoned"
    assert abandon_goal(session.ledger, lone.id) is None


def test_goal_tiered_caps(session):
    """分档额度（2026-09-12）：大目标 ≤2 / 单父下子目标 ≤3 / 未挂靠支线 ≤2。

    关键：子目标额度**按父计数**——挂到主线下面才能多挂。
    """
    led = session.ledger
    b1 = add_goal(led, text="大目标一", kind="big")
    b2 = add_goal(led, text="大目标二", kind="big")
    with pytest.raises(ValueError, match="大目标（主线）已达上限 2 条"):
        add_goal(led, text="大目标三", kind="big")

    for i in range(3):
        add_goal(led, text=f"子{i}", kind="small", big_goal_id=b1.id)
    with pytest.raises(ValueError, match="下已挂 3 个子目标"):
        add_goal(led, text="子溢出", kind="small", big_goal_id=b1.id)
    # 额度按父计数：b1 满了，b2 下面照样能挂
    add_goal(led, text="b2 的子", kind="small", big_goal_id=b2.id)

    add_goal(led, text="孤儿一", kind="small")
    add_goal(led, text="孤儿二", kind="small")
    with pytest.raises(ValueError, match="未挂靠的支线已达上限 2 条"):
        add_goal(led, text="孤儿三", kind="small")


def test_goal_parent_validation(session):
    """挂靠对象必须存在、是大目标、且未闭合；大目标不接受挂靠（层级只有一层）。"""
    led = session.ledger
    small = add_goal(led, text="普通支线", kind="small")
    with pytest.raises(ValueError, match="不是大目标"):
        add_goal(led, text="挂到支线下", kind="small", big_goal_id=small.id)
    with pytest.raises(ValueError, match="不存在"):
        add_goal(led, text="挂到空气", kind="small", big_goal_id="goal_nope")

    big = add_goal(led, text="主线", kind="big")
    complete_goals(led, [big.id])
    with pytest.raises(ValueError, match="已完成，不能再挂子目标"):
        add_goal(led, text="挂到已完成的主线", kind="small", big_goal_id=big.id)

    nested = add_goal(led, text="主线二", kind="big", big_goal_id=big.id)
    assert nested.big_goal_id is None  # 大目标不收挂靠


def test_goal_cascade_frees_slots(session):
    """滚动释放（完成一个子目标就腾名额）+ 父闭合级联释放。"""
    led = session.ledger
    big = add_goal(led, text="主线", kind="big")
    kids = [
        add_goal(led, text=f"子{i}", kind="small", big_goal_id=big.id)
        for i in range(3)
    ]
    with pytest.raises(ValueError, match="下已挂 3 个子目标"):
        add_goal(led, text="子溢出", kind="small", big_goal_id=big.id)

    # 完成一个 → 名额回来
    complete_goals(led, [kids[0].id])
    add_goal(led, text="子新", kind="small", big_goal_id=big.id)

    # 父闭合 → 剩余 active 子目标全部撤下（kids[0] 已 done 不受影响）
    complete_goals(led, [big.id])
    assert kids[0].status == "done"
    assert kids[1].status == "abandoned"
    assert kids[2].status == "abandoned"


def test_goal_abandon_loose_frees_slot(session):
    led = session.ledger
    a = add_goal(led, text="孤儿一", kind="small")
    add_goal(led, text="孤儿二", kind="small")
    with pytest.raises(ValueError, match="未挂靠的支线已达上限"):
        add_goal(led, text="孤儿三", kind="small")
    abandon_goal(led, a.id)
    add_goal(led, text="孤儿三", kind="small")  # 释放后再挂


def test_goals_block_after_preset(session):
    """Goal block appears in the writer order AFTER the preset block and lists
    active goals with their tags; no active goals → no block. NPC goals show
    their owner and get the two-advance-mode discipline."""
    add_goal(session.ledger, text="查明朱明打架的真相", kind="big", npc_id="朱明")
    add_goal(session.ledger, text="让主角答应下周跟她一起去接货", kind="small", subject="王蓉", npc_id="王蓉")
    # 预设文风块（三级）非空，才能断言三级与资料区、一级的相对位置。
    session.world.presets.writer_guidelines = "文风偏好：白描为主，少形容词。"

    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "剧情目标（玩家设立的方向）：" in order
    assert "- [大目标/主线·玩家] 查明朱明打架的真相（关联：朱明）" in order
    assert "- [小目标/支线·王蓉] 让主角答应下周跟她一起去接货（关联：王蓉）" in order
    assert "引导纪律" in order
    # NPC 目标的两形态推进纪律。
    assert "NPC 在场 → 让她自然提及" in order
    assert "NPC 不在场 → 安排她主动来找玩家" in order

    # 位置（2026-09-11）：二级 → 三级 → 一级 连成梯队（在资料区之前），
    # 资料区整体在后（玩家资料 → 剧情目标），事件日志收尾。
    c_pos = order.index("【二级 · 情节合理性】")
    s_pos = order.index("【三级 · 文风与剧情倾向】")
    k_pos = order.index("【一级 · 输出格式】")
    d_pos = order.index("玩家资料：")
    g_pos = order.index("剧情目标")
    assert c_pos < s_pos < k_pos < d_pos < g_pos


def test_goals_block_empty_when_no_active(session):
    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "剧情目标（玩家设立的方向）：" not in order


def test_goal_tree_rendering_progress_and_ids(session):
    """父子层级渲染：大目标带章节进度、子目标缩进、孤儿单列一节；
    编剧视图不吐 id，导演视图吐 id（否则废弃/挂父填不出来）。"""
    led = session.ledger
    big = add_goal(led, text="查明朱明打架的真相", kind="big", npc_id="朱明")
    kid = add_goal(led, text="帮朱明包扎伤口", kind="small", big_goal_id=big.id)
    done_kid = add_goal(led, text="问清打架缘由", kind="small", big_goal_id=big.id)
    complete_goals(led, [done_kid.id])
    loose = add_goal(led, text="学一手做饭", kind="small")

    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "- [大目标/主线·玩家] 查明朱明打架的真相（关联：朱明）　（子目标 1/2 已完成）" in order
    assert "　　· [小目标/支线·玩家] 帮朱明包扎伤口" in order
    assert "未挂靠的支线：" in order
    assert "- [小目标/支线·玩家] 学一手做饭" in order
    assert "**大目标是方向盘**" in order
    # 编剧不需要 id
    assert big.id not in order
    assert kid.id not in order
    assert loose.id not in order

    from app.core.workorder import build_director_chat_system

    director = build_director_chat_system(session.world, session.ledger, "网吧")
    assert f"[{big.id}] [大目标/主线·玩家] 查明朱明打架的真相（关联：朱明）" in director
    assert f"[{kid.id}] [小目标/支线·玩家]" in director


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


def test_audit_goals_tree_has_progress_but_no_hard_gate(session):
    """审计侧呈现层级进度作证据，但明确"只作参考证据、不是判据"。"""
    from app.core.workorder import build_audit_work_order

    led = session.ledger
    big = add_goal(led, text="查明朱明打架的真相", kind="big")
    kid = add_goal(led, text="帮朱明包扎伤口", kind="small", big_goal_id=big.id)
    complete_goals(led, [kid.id])

    order = build_audit_work_order(session.world, led, "主街")
    assert f"[{big.id}] [大目标/主线·玩家] 查明朱明打架的真相——其下子目标 1/1 已完成" in order
    assert "只作参考证据、不是判据" in order
    assert "玩家绕道达成" in order
