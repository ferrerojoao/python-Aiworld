"""剧情目标（M14 主线载体）：建立/废弃/完成结算 + 父子层级。

钩子台账已废弃（2026-09-07 拍板）："玩家看不到、不主动产生剧情"的被动台账
被"玩家在导演窗口设立、编剧写作时主动引导、审计按正文判定完成"的目标系统取代。
本模块只有纯数据操作；目标由导演窗口建立（玩家确认后落账），完成由审计
AuditOutput.completed_goal_ids 在采纳时判定。

父子层级（2026-09-12）：大目标 = 章节（往哪去），小目标 = 节拍（下一步做什么）。
``big_goal_id`` 从此真正参与运转——额度按父计数、渲染成两级树、父闭合时级联
放弃其下子目标。层级只允许一层（大目标不接受挂靠）。
"""

from __future__ import annotations

import datetime as dt

from app.core.store import new_id
from app.ledger.save import Goal

# 分档额度（2026-09-12）：槽位按层级分配，不再是全局的 6 条共享。
# 子目标额度**按父计数**——挂到主线下面才能多挂，这是让父子结构有收益的压力。
MAX_BIG_GOALS = 2  # 大目标（主线）：主线多了就没有主线
MAX_CHILD_GOALS = 3  # 单个大目标下挂靠的 active 子目标：一章三拍
MAX_LOOSE_GOALS = 2  # 未挂靠的 active 支线（无父）：允许自由支线，但不能绕过主线额度


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def active_goals(ledger) -> list[Goal]:
    return [g for g in ledger.save.goals if g.status == "active"]


def children_of(ledger, big_goal_id: str, *, active_only: bool = True) -> list[Goal]:
    kids = [g for g in ledger.save.goals if g.big_goal_id == big_goal_id]
    if active_only:
        kids = [g for g in kids if g.status == "active"]
    return kids


def child_progress(ledger, big_goal_id: str) -> tuple[int, int]:
    """返回（已完成, 计入总数）——放弃（abandoned）的子目标不计数（已从路线图上撤下）。"""
    kids = [
        g
        for g in ledger.save.goals
        if g.big_goal_id == big_goal_id and g.status in {"active", "done"}
    ]
    return sum(1 for k in kids if k.status == "done"), len(kids)


def goal_tree(ledger) -> tuple[list[tuple[Goal, list[Goal]]], list[Goal]]:
    """渲染用分组：返回 ``([(大目标, 其 active 子目标)], [未挂靠支线])``。

    兜底：父已闭合却仍 active 的子目标（正常不该存在，级联会清）一并归入未挂靠，
    宁可多列一行也不让它在工作单里静默消失。
    """
    active = active_goals(ledger)
    bigs = [g for g in active if g.kind == "big"]
    big_ids = {b.id for b in bigs}
    tree = [(b, [g for g in active if g.big_goal_id == b.id]) for b in bigs]
    loose = [
        g
        for g in active
        if g.kind != "big" and (not g.big_goal_id or g.big_goal_id not in big_ids)
    ]
    return tree, loose


def _resolve_parent(ledger, big_goal_id: str) -> Goal:
    """校验挂靠对象：必须存在、是大目标、且未闭合。"""
    parent = next((g for g in ledger.save.goals if g.id == big_goal_id), None)
    if parent is None:
        raise ValueError(f"挂靠的大目标不存在：{big_goal_id}")
    if parent.kind != "big":
        raise ValueError(f"「{parent.text}」不是大目标，不能作为挂靠对象")
    if parent.status != "active":
        state = "已完成" if parent.status == "done" else "已废弃"
        raise ValueError(f"「{parent.text}」{state}，不能再挂子目标")
    return parent


def check_capacity(ledger, *, kind: str, big_goal_id: str | None) -> Goal | None:
    """分档额度校验；返回校验通过的父目标（未挂靠/大目标时为 None）。

    集中在一处，将来若加"改挂"入口直接复用，避免绕过额度。
    """
    active = active_goals(ledger)
    if kind == "big":
        n_big = sum(1 for g in active if g.kind == "big")
        if n_big >= MAX_BIG_GOALS:
            raise ValueError(
                f"大目标（主线）已达上限 {MAX_BIG_GOALS} 条，请先完成或废弃旧的大目标"
            )
        return None
    if big_goal_id:
        parent = _resolve_parent(ledger, big_goal_id)
        n_child = sum(1 for g in active if g.big_goal_id == parent.id)
        if n_child >= MAX_CHILD_GOALS:
            raise ValueError(
                f"「{parent.text}」下已挂 {MAX_CHILD_GOALS} 个子目标，请先完成其中一个"
            )
        return parent
    # 与 goal_tree 同口径计"未挂靠"：凡没挂在 active 大目标下的 active 支线
    # 都算占孤儿额度（含父已失效的兜底情形），避免出现两边都不计数的缝。
    _tree, loose = goal_tree(ledger)
    if len(loose) >= MAX_LOOSE_GOALS:
        raise ValueError(
            f"未挂靠的支线已达上限 {MAX_LOOSE_GOALS} 条——可挂到某个大目标下，或先完成旧的"
        )
    return None


def add_goal(
    ledger,
    *,
    text: str,
    kind: str,
    subject: str = "player",
    big_goal_id: str | None = None,
    npc_id: str | None = None,
) -> Goal:
    """Create a goal (player-confirmed via the director window). Raises when
    the active cap for its tier is exceeded, or the parent link is invalid."""
    kind = "big" if kind == "big" else "small"
    if kind == "big":
        big_goal_id = None  # 层级只允许一层
    check_capacity(ledger, kind=kind, big_goal_id=big_goal_id)
    goal = Goal(
        id=new_id("goal"),
        text=text.strip()[:120],
        kind=kind,
        subject=subject or "player",
        status="active",
        big_goal_id=big_goal_id,
        npc_id=npc_id,
        created_at=_now(),
        done_at=None,
    )
    ledger.save.goals.append(goal)
    return goal


def cascade_close(ledger, goal: Goal) -> list[Goal]:
    """父（大目标）闭合时级联放弃其下仍 active 的子目标。

    父结束了，挂在它下面的支线失去依托；留着会被编剧继续当"下一步"推，
    污染引导。纯数据卫生——不落事件、不进正文、不判剧情。
    """
    closed: list[Goal] = []
    if goal.kind != "big":
        return closed
    for child in ledger.save.goals:
        if child.big_goal_id == goal.id and child.status == "active":
            child.status = "abandoned"
            child.done_at = _now()
            closed.append(child)
    return closed


def abandon_goal(ledger, goal_id: str) -> Goal | None:
    """Abandon an active goal (player decision via the director window)."""
    for goal in ledger.save.goals:
        if goal.id == goal_id and goal.status == "active":
            goal.status = "abandoned"
            goal.done_at = _now()
            cascade_close(ledger, goal)
            return goal
    return None


def complete_goals(ledger, goal_ids: list[str]) -> list[Goal]:
    """Mark active goals as done (the audit judged the adopted prose fulfilled
    them). 大目标完成/废弃后其下子目标一并撤下。"""
    completed: list[Goal] = []
    for goal in ledger.save.goals:
        if goal.id in goal_ids and goal.status == "active":
            goal.status = "done"
            goal.done_at = _now()
            completed.append(goal)
    for goal in completed:
        cascade_close(ledger, goal)
    return completed
