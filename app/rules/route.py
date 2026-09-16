"""移动判定（玩家输入 → 是不是在走向某个地点）。

**本模块只剩移动**。曾经的"跳时意图"解析（日界词 / 钟点词 / 相对天数 → 绝对时刻）
已于 2026-09-16 整体下线：那套词表用正则匹配自由语句，克制规则越补越多（"没等到
明天就走了""三天后我才明白""直到我有一点累"都会被误判成跳时），而且它一旦先定钟，
就必须再经 ``settle_hint`` 通知审计"别再估一遍"，凭空造出一个跨工位的重复计费风险。

现在时间全交审计（``AuditOutput.clock_to`` / ``delta_minutes``）——审计本来就同时
看得到正文与玩家输入，且是唯一的结算方。引擎只保留两件机械的事：
`_accept` 方向守卫（时钟不许倒退）与 ``AUDIT_DELTA_CAP_MINUTES`` 保险丝。
"""

from __future__ import annotations

# 移动动词（剥掉前置连接词/日界词后再判句首）。
MOVE_VERBS = ("去", "回", "走到", "前往", "来到", "赶到", "过去", "回趟", "我去", "我想去", "我要去")

# 以移动动词开头但其实不是移动的说法（"去年…"会命中"去"，"回忆…"会命中"回"）。
_NON_MOVE_HEADS = ("去年", "前天", "回忆", "回顾")

# 日界词：只用于**剥句首**，好让"第二天去学校"露出真正的"去学校"再判移动。
# 它们的时间语义已不在本模块（跳时全交审计）。长词在前（"大后天"先于"后天"）。
DAY_WORDS: tuple[str, ...] = (
    "大后天",
    "第二天",
    "后天",
    "明天",
    "明日",
    "次日",
    "隔天",
    "翌日",
    "来日",
)

# 句首噪音：剥掉后才能正确判断"是不是移动"（"然后去学校" → "去学校"）。
_LEADING_NOISE = ("然后", "接着", "随后", "之后", "等到", "直到", "再", "就")

_PUNCT = "，,。；;：:、！？!? \t"


def _strip_leading_noise(text: str) -> str:
    """只剥句首连接词——用于"日界词是否在句首"的判定。"""
    rest = (text or "").strip().lstrip(_PUNCT)
    changed = True
    while changed and rest:
        changed = False
        for word in _LEADING_NOISE:
            if rest.startswith(word):
                rest = rest[len(word):].lstrip(_PUNCT)
                changed = True
    return rest


def _strip_leading_time(text: str) -> str:
    """再往前剥掉日界词，好让"第二天去学校"露出真正的"去学校"（判移动用）。"""
    rest = _strip_leading_noise(text)
    changed = True
    while changed and rest:
        changed = False
        for word in DAY_WORDS:
            if rest.startswith(word):
                rest = rest[len(word):].lstrip(_PUNCT)
                changed = True
    return rest


def looks_like_move(text: str) -> bool:
    """这一轮玩家是不是在移动（移动与时间无关——时间已全交审计）。"""
    rest = _strip_leading_time(text)
    if rest.startswith(_NON_MOVE_HEADS):
        # "去年我去过学校"不是在移动（旧的 startswith("去") 会把它当移动，
        # 在世界里真有"学校"场景时会静默把玩家搬过去）。
        return False
    return rest.startswith(MOVE_VERBS)
