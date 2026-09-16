"""注入上限（2026-09-16 从世界配置搬到系统设置）—— 三处共用同一份数字。

起因（用户实测）："限制太小导致 NPC 总是忘记事，而 token 因为缓存命中，
消耗没有那么快。" 于是三个数字变成玩家可调的**系统设置**：

    event_log_limit   事件日志取最近几条
    event_log_full    其中最近几条给正文原文（最少 1）
    known_set_limit   每个 NPC 已知集几条

三条纪律：**0 = 给全部**（旧口径"0 = 不给"已废，与玩家直觉相反）；
编剧 / QC / Actor **三处同步**（QC 拿编剧正文比对知识边界，两边条数不一致
会造出"编剧写了、QC 却以为泄漏"的假警报）；数字只在系统设置里有权威版本，
装配函数的签名里不再留默认值。
"""

from app.config import InjectionLimits, Settings
from app.core.workorder import build_actor_work_order, build_work_order
from app.workers.qc import build_qc_reference


def _append(ledger, i: int) -> None:
    """写一条公开叙述事件：summary 短、body 长，便于区分"摘要行"与"原文行"。"""
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": f"2026-07-14T09:{i:02d}:00",
            "location": "主街",
            "participants": ["刘星", "朱明"],
            "known_by": None,
            "body": f"第{i}件事的正文。",
            "summary": f"第{i}件事。",
            "player_input": None,
            "source": "test",
        }
    )


def _writer(session, **limits) -> str:
    return build_work_order(
        "writer",
        session.world,
        session.ledger,
        "主街",
        limits=InjectionLimits(**limits),
    )


# ---------------------------------------------------------------------------
# 事件日志
# ---------------------------------------------------------------------------

def test_event_log_limit_zero_gives_everything(session) -> None:
    """0 = 给全部：最早的事件也进日志（旧口径下 0 等于"一条都不给"）。"""
    for i in range(12):
        _append(session.ledger, i)

    out = _writer(session, event_log_limit=0, event_log_full=3)
    assert "更早事件（摘要）：" in out  # 有摘要段 = 窗口没吃到全部
    assert "第0件事。" in out  # 最早的一条也在（摘要）
    assert "第11件事的正文。" in out  # 最新的给原文


def test_event_log_limit_still_caps_the_window(session) -> None:
    """非 0 = 取最近 N 条：窗口外的整条消失（摘要也不给）。"""
    for i in range(12):
        _append(session.ledger, i)

    out = _writer(session, event_log_limit=5, event_log_full=1)
    assert "第0件事" not in out  # 被挤出窗口
    assert "第11件事的正文。" in out


def test_event_log_full_controls_prose_count(session) -> None:
    """正文条数是省 token 的第一杠杆：其余只给一行摘要。"""
    for i in range(6):
        _append(session.ledger, i)

    one = _writer(session, event_log_limit=0, event_log_full=1)
    three = _writer(session, event_log_limit=0, event_log_full=3)
    assert one.count("的正文。") == 1
    assert three.count("的正文。") == 3


# ---------------------------------------------------------------------------
# 三处同步：QC / Actor 与编剧用同一个数
# ---------------------------------------------------------------------------

def test_qc_reference_uses_the_same_known_limit(session) -> None:
    """QC 是拿编剧的正文去比对知识边界的，条数必须与编剧同步。"""
    for i in range(6):
        _append(session.ledger, i)

    wide = build_qc_reference(session.world, session.ledger, ["朱明"], known_limit=0)
    narrow = build_qc_reference(session.world, session.ledger, ["朱明"], known_limit=1)
    assert "第0件事" in wide
    assert "第0件事" not in narrow


def test_actor_order_uses_the_same_known_limit(session) -> None:
    """Actor 的记忆切片同样跟随系统设置（原先挂 world.meta.memory_limit）。"""
    for i in range(6):
        _append(session.ledger, i)

    def order(limit: int) -> str:
        return build_actor_work_order(
            session.world,
            session.ledger,
            "朱明",
            "主街",
            limits=InjectionLimits(known_set_limit=limit),
        )

    assert "第0件事" in order(0)  # 0 = 全部
    assert "第0件事" not in order(1)  # 只给最近 1 条


# ---------------------------------------------------------------------------
# 配置层
# ---------------------------------------------------------------------------

def test_settings_limits_clamps_illegal_values() -> None:
    """clamp 只在一处收口：负数当 0（不限制），正文条数最少 1。

    界面与 .env 都可能塞进非法值；装配层不该各自防御，否则又是几套口径。
    """
    got = Settings(event_log_limit=-5, event_log_full=0, known_set_limit=-1).limits()
    assert (got.event_log_limit, got.event_log_full, got.known_set_limit) == (0, 1, 0)
