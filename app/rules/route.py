from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from app.world.models import WorldContent

# 次日默认钟点的兜底值：世界 start_time 的小时优先（零新增配置），坏/空才用这个。
DEFAULT_DAY_START_HOUR = 8

# 采信窗口：世界 start_time 的小时只有落在这段里才当作"该世界的日常起点"。
# 夜里开局的世界（Reno = 21:40）若照搬 21，"第二天"会落成次日 21:00 —— 明显不是
# "第二天"的意思，所以窗口外一律退回 DEFAULT_DAY_START_HOUR。
DAY_START_WINDOW = (6, 11)

# 移动动词（剥掉前置时间词后再判句首）。移动与跳时是**正交**两件事，
# 不再共用一个单标签 route —— "第二天去学校"两者同时成立。
MOVE_VERBS = ("去", "回", "走到", "前往", "来到", "赶到", "过去", "回趟", "我去", "我想去", "我要去")

# 以移动动词开头但其实不是移动的说法（"去年…"会命中"去"，"回忆…"会命中"回"）。
_NON_MOVE_HEADS = ("去年", "前天", "回忆", "回顾")

# 显式日界词 → 相对今天的天数。长词在前（"大后天"必须先于"后天"命中）。
DAY_WORDS: tuple[tuple[str, int], ...] = (
    ("大后天", 3),
    ("第二天", 1),
    ("后天", 2),
    ("明天", 1),
    ("明日", 1),
    ("次日", 1),
    ("隔天", 1),
    ("翌日", 1),
    ("来日", 1),
)

# 时辰词 → 墙钟小时。数值钟点（"晚上八点"）优先于这里。
CLOCK_WORDS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("凌晨",), 5),
    (("清晨", "一早", "破晓"), 6),
    (("早上", "早晨", "上午"), 8),
    (("中午", "正午", "晌午"), 12),
    (("下午", "午后"), 14),
    (("傍晚", "黄昏", "日落", "日暮"), 18),
    (("晚上", "夜晚", "夜里", "入夜"), 19),
    (("深夜", "半夜", "子夜"), 23),
)

# 午后制限定词（"下午三点" = 15:00）；十二点该算午夜的口径单列
# （"晚上十二点""夜里十二点"都是 00:00，不是正午）。
_PM_WORDS = ("下午", "午后", "傍晚", "黄昏", "日落", "日暮", "晚上", "夜晚", "夜里", "入夜")
_MIDNIGHT_WORDS = ("深夜", "半夜", "子夜", "凌晨", "夜里", "夜晚", "晚上", "入夜")
_CLOCK_WORD_HEADS = tuple(word for words, _ in CLOCK_WORDS for word in words)

# "跳时意图"触发词：明确表示"等/跳到某个时刻"。
_TRIGGERS = ("等到", "跳到", "直到", "再到", "一直等到", "捱到", "熬到")

# 句首噪音：剥掉后才能正确判断"是不是移动"（"第二天去学校" → "去学校"）。
_LEADING_NOISE = ("然后", "接着", "随后", "之后", "等到", "直到", "再", "就")

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_NUM = r"[0-9零〇一二两三四五六七八九十]{1,3}"
_CLOCK_NUM_RE = re.compile(rf"({_NUM})\s*点\s*(半)?")
_HHMM_RE = re.compile(r"([0-9]{1,2})\s*[:：]\s*([0-9]{2})")
_REL_AFTER_RE = re.compile(rf"({_NUM})\s*(?:个)?\s*(天|日|周|星期|月|年)\s*(?:后|之后|以后|过后)")
_REL_ELAPSED_RE = re.compile(rf"(?:过了|隔了|又过|等了)\s*({_NUM})\s*(?:个)?\s*(天|日|周|星期|月|年)")

_UNIT_DAYS = {"天": 1, "日": 1, "周": 7, "星期": 7, "月": 30, "年": 365}
_PUNCT = "，,。；;：:、！？!? \t"


@dataclass(frozen=True)
class TimeIntent:
    """规则侧识别到的跳时意图：玩家明示意图 + 结算出的**绝对时刻**。

    产出绝对时刻而不是"时长"，是为了让"次日"这类意图不被当前钟点污染
    （原来 +720min 从 09:25 出发落到当日 21:25，而意图明明是次日早上）。
    """

    settle_to: str  # ISO 绝对时刻
    label: str  # 人话标签（进编剧/审计提示词）
    kind: str  # day_word | relative_days | clock_word


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _parse_dt(value: str | None) -> dt.datetime | None:
    """宽松解析时钟串；带时区一律拍成 naive（架空世界的本地墙钟）。"""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed.replace(microsecond=0, tzinfo=None)


def day_start_hour(world: WorldContent) -> int:
    """次日默认钟点 = 世界 start_time 的小时（落在 DAY_START_WINDOW 内才采信）。

    零新增配置：改世界起点即改"第二天"落在几点。但起点不一定在早上——
    Reno 是 21:40 夜里开局，照搬会得到"次日 21:00"，所以窗口外一律退回 8 点。
    """
    parsed = _parse_dt(world.meta.start_time)
    if parsed is not None and DAY_START_WINDOW[0] <= parsed.hour <= DAY_START_WINDOW[1]:
        return parsed.hour
    return DEFAULT_DAY_START_HOUR


def _to_int(token: str) -> int | None:
    """中文/阿拉伯数词 → int（只支持 0~99，够小时与天数用）。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if not all(ch in _CN_DIGITS for ch in token):
        return None
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_DIGITS[head] if head else 1
        ones = _CN_DIGITS[tail] if tail else 0
        return tens * 10 + ones
    value = 0
    for ch in token:
        value = value * 10 + _CN_DIGITS[ch]
    return value


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
        for word, _ in DAY_WORDS:
            if rest.startswith(word):
                rest = rest[len(word):].lstrip(_PUNCT)
                changed = True
    return rest


def looks_like_move(text: str) -> bool:
    """这一轮玩家是不是在移动（与是否跳时无关，两者可同时成立）。"""
    rest = _strip_leading_time(text)
    if rest.startswith(_NON_MOVE_HEADS):
        # "去年我去过学校"不是在移动（旧的 startswith("去") 会把它当移动，
        # 在世界里真有"学校"场景时会静默把玩家搬过去）。
        return False
    return rest.startswith(MOVE_VERBS)


# ---------------------------------------------------------------------------
# 时间意图
# ---------------------------------------------------------------------------


def _day_word_commits(text: str, word: str) -> bool:
    """日界词是否真的在表达跳时（防"明天的事明天再说"这类闲聊误推时钟）。

    成立条件（任一）：词后没有别的内容（"第二天" 整句即此）／词后紧跟移动动词
    （"第二天去学校"）／词后紧跟时辰词（"明天早上"）。
    注意"句首"本身**不算**充分条件——"明天见""明天的事明天再说"都在句首。
    """
    idx = text.find(word)
    tail = text[idx + len(word):].lstrip(_PUNCT)
    if not tail:
        return True
    if tail.startswith(MOVE_VERBS):
        return True
    return tail.startswith(_CLOCK_WORD_HEADS)


def _find_day_word(text: str, stripped: str) -> tuple[str, int] | None:
    """显式日界词。带触发词时允许出现在句中，否则必须在句首，或恰好收在句尾/引出动作。

    这条克制是刻意的：假阳（闲聊把时钟推走）比假阴（意图漏给审计）严重得多，
    而且现在还没有回退机制可救。
    """
    triggered = any(word in text for word in _TRIGGERS)
    for word, days in DAY_WORDS:
        if triggered and word in text:
            return word, days
        if word in stripped and _day_word_commits(stripped, word):
            return word, days
    return None


def _find_relative_days(text: str) -> tuple[int, str] | None:
    """相对日界：`三天后` / `过了两天` / `一个月后` → (天数, 命中原文)。"""
    for pattern in (_REL_AFTER_RE, _REL_ELAPSED_RE):
        match = pattern.search(text)
        if not match:
            continue
        number = _to_int(match.group(1))
        unit = match.group(2)
        if number is None or unit not in _UNIT_DAYS:
            continue
        return number * _UNIT_DAYS[unit], match.group(0)
    return None


def _find_clock_point(text: str) -> tuple[int, int, str] | None:
    """句中的钟点 → (小时, 分钟, 时辰名)。数值钟点优先于时辰词。"""
    match = _HHMM_RE.search(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute, f"{hour:02d}:{minute:02d}"

    match = _CLOCK_NUM_RE.search(text)
    if match:
        hour = _to_int(match.group(1))
        if hour is not None and 0 <= hour <= 24:
            minute = 30 if match.group(2) else 0
            prefix = text[max(0, match.start() - 4): match.start()]
            if any(word in prefix for word in _PM_WORDS) and 1 <= hour < 12:
                hour += 12
            elif any(word in prefix for word in _MIDNIGHT_WORDS) and hour == 12:
                hour = 0
            if 0 <= hour <= 23:
                return hour, minute, f"{hour:02d}:{minute:02d}"

    for words, hour in CLOCK_WORDS:
        for word in words:
            if word in text:
                return hour, 0, word
    return None


def parse_time_intent(
    text: str, clock: str, *, day_start: int = DEFAULT_DAY_START_HOUR
) -> TimeIntent | None:
    """把玩家的跳时意图结算成**绝对时刻**；识别不出返回 None（全交审计）。

    优先级：显式日界词（第二天/明天/后天）> 相对日界（三天后/过了两天）> 钟点词。
    钟点词单独出现不算意图——必须配合 ``等到/跳到/直到`` 触发词，否则
    "晚上你想吃什么""我有一点累"都会把时钟推走。
    """
    text = (text or "").strip()
    if not text:
        return None
    now = _parse_dt(clock)
    if now is None:
        # 当前钟点读不出来就算不出绝对时刻，交审计兜底。
        return None

    stripped = _strip_leading_noise(text)
    day_word = _find_day_word(text, stripped)
    relative = _find_relative_days(text) if day_word is None else None
    triggered = any(word in text for word in _TRIGGERS)
    if day_word is None and relative is None and not triggered:
        return None

    point = _find_clock_point(text)

    if day_word is not None:
        word, days = day_word
        hour, minute = (point[0], point[1]) if point else (day_start, 0)
        # 数值钟点的 name 已经是 "HH:MM"，别再拼一遍（"次日08:00 08:00"）。
        name = point[2] if point and point[2] in _CLOCK_WORD_HEADS else ""
        target = dt.datetime.combine((now + dt.timedelta(days=days)).date(), dt.time(hour, minute))
        return TimeIntent(
            settle_to=target.isoformat(),
            label=f"{word}{name} {hour:02d}:{minute:02d}",
            kind="day_word",
        )

    if relative is not None:
        days, raw = relative
        hour, minute = (point[0], point[1]) if point else (now.hour, now.minute)
        target = dt.datetime.combine((now + dt.timedelta(days=days)).date(), dt.time(hour, minute))
        return TimeIntent(
            settle_to=target.isoformat(),
            label=f"{raw} {hour:02d}:{minute:02d}",
            kind="relative_days",
        )

    if point is None:
        return None

    hour, minute, raw_name = point
    name = raw_name if raw_name in _CLOCK_WORD_HEADS else ""
    target = dt.datetime.combine(now.date(), dt.time(hour, minute))
    if target <= now:
        # "等到中午"在下午说 → 次日中午（意图是往前等，不是倒回过去）。
        target += dt.timedelta(days=1)
        prefix = "次日"
    else:
        prefix = "今日"
    return TimeIntent(
        settle_to=target.isoformat(),
        label=f"{prefix}{name} {hour:02d}:{minute:02d}",
        kind="clock_word",
    )
