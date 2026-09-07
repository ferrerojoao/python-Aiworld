"""剧情目标（M14 主线载体）：建立/废弃/完成结算。

钩子台账已废弃（2026-09-07 拍板）："玩家看不到、不主动产生剧情"的被动台账
被"玩家在导演窗口设立、编剧写作时主动引导、审计按正文判定完成"的目标系统取代。
本模块只有纯数据操作；目标由导演窗口建立（玩家确认后落账），完成由审计
AuditOutput.completed_goal_ids 在采纳时判定。
"""

from __future__ import annotations

import datetime as dt

from app.core.store import new_id
from app.ledger.save import Goal

# 开放目标上限：达到上限后导演窗口应提示先完成/废弃旧目标。
MAX_ACTIVE_GOALS = 6


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def add_goal(
    ledger,
    *,
    text: str,
    kind: str,
    subject: str = "player",
    big_goal_id: str | None = None,
    npc_id: str | None = None,
) -> Goal:
    """Create a goal (player-confirmed via the director window). Returns None
    implicitly impossible: raising when the active cap is exceeded."""
    active = [g for g in ledger.save.goals if g.status == "active"]
    if len(active) >= MAX_ACTIVE_GOALS:
        raise ValueError(f"活动目标已达上限 {MAX_ACTIVE_GOALS} 条，请先完成或废弃旧目标")
    goal = Goal(
        id=new_id("goal"),
        text=text.strip()[:120],
        kind="big" if kind == "big" else "small",
        subject=subject or "player",
        status="active",
        big_goal_id=big_goal_id,
        npc_id=npc_id,
        created_at=_now(),
        done_at=None,
    )
    ledger.save.goals.append(goal)
    return goal


def abandon_goal(ledger, goal_id: str) -> Goal | None:
    """Abandon an active goal (player decision via the director window)."""
    for goal in ledger.save.goals:
        if goal.id == goal_id and goal.status == "active":
            goal.status = "abandoned"
            goal.done_at = _now()
            return goal
    return None


def complete_goals(ledger, goal_ids: list[str]) -> list[Goal]:
    """Mark active goals as done (the audit judged the adopted prose fulfilled
    them)."""
    completed: list[Goal] = []
    for goal in ledger.save.goals:
        if goal.id in goal_ids and goal.status == "active":
            goal.status = "done"
            goal.done_at = _now()
            completed.append(goal)
    return completed


def active_goals(ledger) -> list[Goal]:
    return [g for g in ledger.save.goals if g.status == "active"]
