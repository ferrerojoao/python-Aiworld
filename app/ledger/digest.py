"""大总结：把事件流压缩成「一条总结 + K 条位置重建事件」（2026-10-10）。

**它解决的病**：事件流只增不减，几十上百轮之后，注入侧的最近 N 条里挤满早已
无关的旧事，而真正新鲜的只占几条。大总结把"之前发生过的所有事"浓缩成一条，
让玩家轻装上阵继续下面的剧情。

**它不改的东西**（这条边界是硬约束）：世界钟、角色状态、目标、退场标记、未落卡
计数、地点候选册**一律不动**——那些是"世界的现状"，不是"事件流"。压缩事件流
不该让世界回退。

**三条纪律**（每条都有非做不可的理由）：

1. **先重建位置、再丢旧事件**。``present_at`` / ``where_is`` 只认事件，而事件一
   清，在场推导就地塌：编剧不知道谁在这场戏里、Actor 一个都不派（``_actor_ticket``
   是硬闸门）、场景描述变成"四下无人"、"去找某人"的确定性寻路失效。所以必须把
   每个人的"最后位置事实"**先**算出来补回去。
2. **位置重建按 ``(地点, 时刻)`` 分组，不是只按地点**。``Ledger._index`` 里一条带
   位置的事件会**一次性**给它所有 ``participants`` 定同一个位置**和同一个 ``at``**
   ⇒ 把最后目击时刻不同的两个人合进一条，就篡改了其中一人的新鲜度，编剧那句
   "久未见面的要考虑他是否还在"跟着失真。
3. **不可逆 ⇒ 先归档**。大总结销毁原始正文，所以落盘前把旧的 ``events.jsonl``
   挪进 ``archive/``（那个目录**不进导出包**，见 ``_SKIP_DIRS``），考古有据。

**多次大总结 = 滚动**：新总结的原料 = 上一条总结的**正文全文**（当作"已有背景"）
+ 此后新增的事件。旧总结不留在事件流里（它已被新总结吸收），所以事件流永远只有
一条真实总结。喂全文而不是喂摘要的摘要，是**信息不逐轮衰减**的关键。
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from app.core.store import append_event
from app.ledger.events import SOURCE_DIGEST, SOURCE_DIGEST_LOCATION
from app.ledger.queries import Ledger

ARCHIVE_DIRNAME = "archive"


def previous_digest(ledger: Ledger) -> dict[str, Any] | None:
    """上一条总结事件（若有）。事件流里至多一条。"""
    for event in reversed(ledger.narratives):
        if event.get("source") == SOURCE_DIGEST:
            return event
    return None


def pending_events(ledger: Ledger) -> list[dict[str, Any]]:
    """待浓缩的事件 = 除"上一条总结"以外的全部叙事事件。

    上一条总结**不进原料**：它的正文会作为"已有背景"单独喂给模型。混进来的话，
    模型会把"我之前总结过一遍"也当成一件发生过的事写进新总结，越滚越玄。
    """
    return [ev for ev in ledger.narratives if ev.get("source") != SOURCE_DIGEST]


def digest_source(ledger: Ledger) -> tuple[str, list[dict[str, Any]]]:
    """起草 worker 的原料：``(上一条总结的正文, 待浓缩事件)``。"""
    previous = previous_digest(ledger)
    return ((previous.get("body") or "") if previous else ""), pending_events(ledger)


def location_rebuild_events(ledger: Ledger) -> list[dict[str, Any]]:
    """按 ``(地点, 时刻)`` 为每个人的最后位置事实重建一条**管线事件**。

    ``id`` 留空，由 :func:`apply_digest` 统一分配。排序显式化（按 ``at``、``location``）：
    分组来自 ``last_location`` 的迭代序，而那是事件的先后序——不显式排，同一批
    事件在不同平台上会不会给出不同的包/不同的日志顺序就说不准了。
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for name, event in ledger.last_location.items():
        # 空名字要挡：审计的 participants 是 LLM 给的、事件落盘又**不做校验**
        # （看 app/ledger/events.py），一个 "" 会造出一条没有主体的位置事件。
        if not name:
            continue
        runtime = ledger.save.entities.get(name)
        if runtime is not None and runtime.lifecycle == "retired":
            continue  # 退场者引擎已跳过（present_at），重建他的位置只是噪音
        # 这里**不需要**再判 location 是否为空：`last_location` 唯一的写入点
        # （Ledger._index）两处都在 `if loc:` 之内，取值必真。
        # （2026-10-10 曾有一条 `if not location: continue`，变异测试证明它是死重量，已删。）
        location = event.get("location") or ""
        groups.setdefault((location, str(event.get("at") or "")), []).append(name)
    out: list[dict[str, Any]] = []
    for (location, at), names in groups.items():
        ordered = sorted(names)
        out.append(
            {
                "id": "",
                "kind": "narrative",
                "at": at,
                "location": location,
                "participants": ordered,
                "known_by": [],  # 永不进 by_knower（_index 对管线早退），这里取保守值
                "body": "",
                "summary": f"{'、'.join(ordered)}此刻在{location}",
                "player_input": None,
                "source": SOURCE_DIGEST_LOCATION,
            }
        )
    out.sort(key=lambda ev: (ev["at"], ev["location"]))
    return out


def digest_overview(ledger: Ledger) -> dict[str, Any]:
    """给界面的现状：有多少事件、上一条总结覆盖到什么、还能不能总结。"""
    previous = previous_digest(ledger)
    pending = pending_events(ledger)
    return {
        "clock": ledger.save.clock or "",
        "event_count": len(ledger.narratives),
        "pending_count": len(pending),
        "previous": (
            {
                "revision": int(previous.get("digest_revision") or 1),
                "at": previous.get("at") or "",
                "summary": previous.get("summary") or "",
                "text": previous.get("body") or "",
            }
            if previous
            else None
        ),
    }


def _archive_path(ledger: Ledger) -> Path:
    archive = Path(ledger.save_dir) / ARCHIVE_DIRNAME
    archive.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = archive / f"events-{stamp}.jsonl"
    # 同一秒内连做两次（或时钟被拨回）不应互相覆盖。
    index = 1
    while target.exists():
        target = archive / f"events-{stamp}-{index}.jsonl"
        index += 1
    return target


def apply_digest(ledger: Ledger, *, text: str, summary: str = "", clock: str = "") -> dict[str, Any]:
    """把事件流压成「一条总结 + K 条位置重建」。**大总结唯一的写入点。**

    ``text`` = 总结正文（玩家可以改过草稿再交上来），``summary`` 空则取正文前 40 字。

    顺序是硬的：**先算重建、再丢旧事件**——位置快照只在旧事件上算得出来。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("总结正文不能为空")
    previous = previous_digest(ledger)
    revision = int((previous or {}).get("digest_revision") or 0) + 1
    at = clock or ledger.save.clock or ""
    summary = (summary or "").strip() or text[:40]

    rebuild = location_rebuild_events(ledger)
    digest_event = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": at,
        "location": None,
        "participants": [],
        "known_by": None,
        "body": text,
        "summary": summary,
        "player_input": None,
        "source": SOURCE_DIGEST,
        "digest_revision": revision,
    }
    for event in rebuild:
        event["id"] = ledger.allocate_event_id()

    events_path = Path(ledger.events_path)
    archived = ""
    if events_path.exists():
        target = _archive_path(ledger)
        shutil.move(str(events_path), str(target))
        archived = target.name

    ledger.events = [digest_event, *rebuild]
    ledger._rebuild_indexes()
    for event in ledger.events:
        append_event(events_path, event)

    # 改判覆盖以 event_id 为键：旧事件没了，覆盖全成死键（且会让 save.json 无限
    # 膨胀），一并清掉。`StateItem.source_event` 的悬空 id 无害——撤销按 state id
    # 定位（`Transaction._find_state`），那条引用只作"史上存在过"的痕迹留着。
    ledger.save.access_overrides = {}
    ledger.persist_save()
    return {
        "revision": revision,
        "digest_id": digest_event["id"],
        "event_count": len(ledger.events),
        "location_events": len(rebuild),
        "archived": archived,
        "at": at,
        "summary": summary,
    }
