from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class NarrativeRecord(BaseModel):
    id: str
    kind: Literal["narrative"] = "narrative"
    at: str
    location: str | None = None
    participants: list[str] = Field(default_factory=list)
    known_by: list[str] | None = None  # None means public
    body: str
    summary: str | None = None
    player_input: str | None = None  # the player's words that produced this event
    source: str = "turn"


EventRecord = NarrativeRecord

# ---------------------------------------------------------------------------
# 事件来源（``source``）的已知取值
#
# ``source`` 是**一手事实**，唯一的判据消费点是 ``Ledger.game_started()``（谁算
# "本局已开演"）。这里把字面量收成常量，是因为 2026-10-10 加了大总结之后，
# ``source`` 从"记录谁写的"变成了"还要区分**管线**与史实"——字面量散落各处时
# 那条分界线就会在两处漂。
# ---------------------------------------------------------------------------
SOURCE_OPENING = "opening"  # 开场白（重置后重建的那条）
SOURCE_TURN = "turn"  # 回合正文 / NPC 移动 / 退场留痕
SOURCE_STATE = "state"  # 角色状态的增删改（记账事件）
SOURCE_DIRECTOR = "director"  # 导演覆写与记忆注入
SOURCE_DIGEST = "digest"  # 大总结：一条浓缩全部旧史的事件
SOURCE_DIGEST_LOCATION = "digest_location"  # 大总结的**管线**：位置重建事件

# **管线事件**：引擎自己造出来喂索引的，不是世界里发生过的事。
#
# 目前只有一种——大总结的**位置重建事件**。大总结会把事件流压缩掉，而
# ``last_location``（在场推导的唯一地基）正是从事件流现算的；不补回位置，
# 压缩完所有人都会从所有场景消失。所以这些事件**必须进 ``last_location``**，
# 但**绝不能进** narratives / by_location / by_participant / by_knower ——
# 放它们进那四处只会让提示词与事件日志多出玩家看不懂的行（判据落在
# ``Ledger._index`` 一处，见 app/ledger/queries.py）。
DIGEST_PLUMBING_SOURCES = frozenset({SOURCE_DIGEST_LOCATION})


def is_digest_plumbing(event: dict[str, Any]) -> bool:
    """这条事件是不是大总结的管线（只喂位置小抄、对玩家不可见）。"""
    return str(event.get("source") or "") in DIGEST_PLUMBING_SOURCES
