"""规则侧**移动**判定的词表契约。

2026-09-16 去规则化后，`app/rules/route.py` 只剩移动一件事（跳时意图解析整条
下线，时间全交审计）。这些用例就是那块词表的可执行版本：改动 MOVE_VERBS /
DAY_WORDS / _LEADING_NOISE 前，先在这里对齐。
"""

from __future__ import annotations

import pytest

from app.rules.route import looks_like_move


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("去鱼市", True),
        ("第二天去鱼市", True),  # 前置时间词剥掉后仍是移动
        ("明天我去鱼市", True),
        ("然后去学校", True),  # 前置连接词剥掉后仍是移动
        ("我不想去学校", False),
        ("去年我去过学校", False),  # 旧的 startswith("去") 会把它误判为移动
        ("回忆往事", False),
        ("", False),
    ],
)
def test_looks_like_move(text: str, expected: bool) -> None:
    assert looks_like_move(text) is expected
