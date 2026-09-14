"""规则侧时间意图 / 移动意图的词表契约（2026-09-14）。

这些用例就是"词表"本身的可执行版本：改动 `app/rules/route.py` 的任何词条、
钟点表或克制规则，都必须先在这里对齐。
"""

from __future__ import annotations

import pytest

from app.rules.route import day_start_hour, looks_like_move, parse_time_intent
from app.world.models import WorldContent, WorldInfo

NOW = "2026-07-14T09:25:00"


def _world(start_time: str) -> WorldContent:
    return WorldContent(meta=WorldInfo(id="t", name="t", start_time=start_time))


@pytest.mark.parametrize(
    ("start_time", "expected"),
    [
        # 窗口内（06–11）→ 采信世界起点的小时
        ("2026-07-14T06:00:00", 6),
        ("2001-07-10T08:00:00", 8),  # qingshi2
        ("1998-09-01T10:00:00", 10),  # shenshan
        ("2026-07-14T11:00:00", 11),
        # 窗口外（夜里/凌晨开局）→ 退回 8：照搬会得到"次日 21:00"这种明显不对的结果
        ("2026-07-14T05:59:00", 8),
        ("2026-07-14T12:00:00", 8),
        ("2241-08-12T21:40:00", 8),  # Reno
        ("", 8),  # 没写
        ("不是时间", 8),  # 坏值
    ],
)
def test_day_start_hour(start_time: str, expected: int) -> None:
    assert day_start_hour(_world(start_time)) == expected


def test_day_start_hour_drives_next_day() -> None:
    """"第二天"落几点由世界的 start_time 决定（窗口之外退回 8 点）。"""
    morning = parse_time_intent(
        "第二天", "2001-07-10T20:00:00", day_start=day_start_hour(_world("2001-07-10T08:00:00"))
    )
    assert morning is not None and morning.settle_to == "2001-07-11T08:00:00"

    night = parse_time_intent(
        "第二天", "2241-08-12T22:00:00", day_start=day_start_hour(_world("2241-08-12T21:40:00"))
    )
    assert night is not None and night.settle_to == "2241-08-13T08:00:00"  # 不是 21:00


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # —— 显式日界词 → 次日（钟点取世界 start_time 的小时，这里显式给 8）——
        ("第二天去学校", "2026-07-15T08:00:00"),
        ("明天我去网吧", "2026-07-15T08:00:00"),
        ("次日", "2026-07-15T08:00:00"),
        ("睡到第二天", "2026-07-15T08:00:00"),
        ("到了第二天早上", "2026-07-15T08:00:00"),
        ("明天早上", "2026-07-15T08:00:00"),
        ("后天", "2026-07-16T08:00:00"),
        ("大后天下午", "2026-07-17T14:00:00"),
        # 日界词 + 时辰词：以时辰为准（原来"明天，等到中午"会落 +720 分钟）
        ("明天，等到中午", "2026-07-15T12:00:00"),
        ("等到第二天早上八点", "2026-07-15T08:00:00"),
        # —— 时辰词：当日该时刻，已过则次日 ——
        ("等到中午", "2026-07-14T12:00:00"),
        ("等到晚上", "2026-07-14T19:00:00"),
        ("等到深夜", "2026-07-14T23:00:00"),
        ("等到下午三点", "2026-07-14T15:00:00"),
        ("等到20:30", "2026-07-14T20:30:00"),
        # 08:00 已过 → 次日（原来落 60 分钟兜底，把"等到八点"拍成一小时）
        ("等到八点", "2026-07-15T08:00:00"),
        # "夜里十二点"是午夜，不是正午
        ("等到夜里十二点", "2026-07-15T00:00:00"),
        # —— 相对日界 ——
        ("过了三天", "2026-07-17T09:25:00"),
        ("我等了三天", "2026-07-17T09:25:00"),
        ("三天后的早上", "2026-07-17T08:00:00"),
        ("一个月后", "2026-08-13T09:25:00"),
    ],
)
def test_parse_time_intent(text: str, expected: str) -> None:
    intent = parse_time_intent(text, NOW, day_start=8)
    assert intent is not None, text
    assert intent.settle_to == expected


@pytest.mark.parametrize(
    "text",
    [
        "过了一会儿",  # 识别不出 → 什么都不给，全交审计
        "明天的事明天再说",  # 提到"明天"但不是跳时
        "明天见",
        "明天的事",
        "来日方长",
        "晚上你想吃什么",  # 钟点词单独出现不算意图
        "我有一点累",
        "下午三点我再去",  # 没有 等到/跳到 触发词 → 克制（交审计）
        "我不想去学校",
    ],
)
def test_no_false_positive_time_intent(text: str) -> None:
    """假阳（闲聊把时钟推走）比假阴严重得多——目前还没有回退机制可救。"""
    assert parse_time_intent(text, NOW, day_start=8) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("去鱼市", True),
        ("第二天去鱼市", True),  # 前置时间词剥掉后仍是移动
        ("明天我去鱼市", True),
        ("我不想去学校", False),
        ("去年我去过学校", False),  # 旧的 startswith("去") 会把它误判为移动
        ("回忆往事", False),
    ],
)
def test_looks_like_move(text: str, expected: bool) -> None:
    assert looks_like_move(text) is expected


def test_missing_clock_returns_none() -> None:
    """当前钟点读不出来就不猜绝对时刻，整段交审计。"""
    assert parse_time_intent("第二天去学校", "") is None
