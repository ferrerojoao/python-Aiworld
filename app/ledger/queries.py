from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.store import append_event, read_events, read_json, write_json_atomic
from app.ledger.save import LocationCandidate, SaveData
from app.world.models import WorldContent

ADHOC_REGION = "adhoc"  # 一次性布景的归属哨兵：不进任何听域的风闻通道


class Ledger:
    """Event stream + save.json in-memory facade.

    The event stream is the source of truth; save.json holds engine runtime
    state, player preset overrides and entity runtime patches. Derived
    indexes (last_location / by_participant / by_knower) are maintained
    incrementally on append and rebuilt fully on load/reset — they are
    accelerations, never a second source of truth (design: 查询派生，索引加速).
    """

    def __init__(self, world: WorldContent, save_dir: str | Path):
        self.world = world
        self.save_dir = Path(save_dir)
        self.events_path = self.save_dir / "events.jsonl"
        self.save_path = self.save_dir / "save.json"

        raw_save = read_json(self.save_path, None)
        if raw_save is None:
            save_name = self.save_dir.name
            self.save = SaveData(
                meta={
                    "world_id": world.meta.id,
                    "save_name": save_name,
                    "created_at": "",
                    "next_event_id": 1,
                }
            )
        else:
            self.save = SaveData.model_validate(raw_save)

        self.events: list[dict[str, Any]] = read_events(self.events_path)
        self._pending_events: list[dict[str, Any]] = []
        self._calibrate_event_counter()
        self._rebuild_indexes()

    # ------------------------------------------------------------------
    # Indexing (derived, rebuilt on load/reset, updated on append)
    # ------------------------------------------------------------------
    def _rebuild_indexes(self) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_location: dict[str, list[dict[str, Any]]] = {}
        self.narratives: list[dict[str, Any]] = []
        # 小抄①：每个实体（主角与 NPC 同权，键都是中文名）当前由最近一条定位事件确定的位置
        self.last_location: dict[str, dict[str, Any]] = {}
        # 小抄②：每个人参与过哪些事件（known_set / 亲历通道的热路径）
        self.by_participant: dict[str, list[dict[str, Any]]] = {}
        # 小抄③：私密事件按 known_by 成员挂账（知情即可召回）；
        # _knower_members 记录每个事件当前挂了哪些成员，改判时同步增删。
        self.by_knower: dict[str, list[dict[str, Any]]] = {}
        self._knower_members: dict[str, set[str]] = {}
        # 索引命中率统计（展示用，量测"小抄"是否在起作用）
        self.cache_stats: dict[str, int] = {"hits": 0, "misses": 0}

        for event in self.events:
            self._index(event)

    def _calibrate_event_counter(self) -> None:
        """事件流水是权威：加载时按流水里实际用过的最大号校准计数器。

        采纳落盘是"先写流水、再写存档"背靠背两笔——若崩在两笔之间，
        存档里的 next_event_id 是旧值，直接沿用会复用事件号（同号互相
        覆盖、改判/目标等按号引用全部错位）。校准让该窗口无害。
        """
        max_seq = 0
        for event in self.events:
            match = re.fullmatch(r"ev_(\d+)", str(event.get("id", "")))
            if match:
                max_seq = max(max_seq, int(match.group(1)))
        if max_seq + 1 > self.save.meta.next_event_id:
            self.save.meta.next_event_id = max_seq + 1

    def _index(self, event: dict[str, Any]) -> None:
        """Update in-memory indexes for one event (append and rebuild both)."""
        self.by_id[event["id"]] = event
        if event["kind"] != "narrative":
            return
        self.narratives.append(event)
        loc = event.get("location")
        if loc:
            self.by_location.setdefault(loc, []).append(event)
        # 小抄①：带位置的新事件，确定了其参与者此刻的位置
        if loc:
            for pid in event.get("participants", []):
                self.last_location[pid] = event
        # 小抄②：参与者索引
        for pid in event.get("participants", []):
            self.by_participant.setdefault(pid, []).append(event)
        # 小抄③：知情名单索引（私密事件）
        self._reindex_knowers(event)

    def _reindex_knowers(self, event: dict[str, Any]) -> None:
        """(Re)index one event's by_knower entries from its *effective* known_by.

        Access overrides take precedence over the raw field (same rule as
        visible_to). Called on append and again whenever an override changes,
        so a rejudge that adds/removes a knower keeps recall aligned with
        visibility: 谁能引用，谁就能被召回.
        """
        ev_id = event["id"]
        for member in self._knower_members.pop(ev_id, set()):
            entries = self.by_knower.get(member)
            if entries:
                remaining = [e for e in entries if e["id"] != ev_id]
                if remaining:
                    self.by_knower[member] = remaining
                else:
                    del self.by_knower[member]
        known_by = self.effective_known_by(event)
        members: set[str] = set()
        if known_by is not None:
            for member in known_by:
                self.by_knower.setdefault(member, []).append(event)
                members.add(member)
        self._knower_members[ev_id] = members

    def reindex_knowers(self, event_id: str) -> None:
        """Public hook for access-control writes: rebuild by_knower for one event."""
        event = self.by_id.get(event_id)
        if event is not None and event["kind"] == "narrative":
            self._reindex_knowers(event)

    # ------------------------------------------------------------------
    # 访问状态（改判）：唯一的权威表达式
    # ------------------------------------------------------------------
    def effective_known_by(self, event: dict[str, Any]) -> list[str] | None:
        """事件的**有效**知情名单：改判覆盖优先于落库时的原值。

        这是访问控制的**唯一权威**——``visible_to`` / ``known_set`` / ``by_knower``
        索引 / 事件日志视图全部走它。**别在任何地方再写一遍这个 .get(…, …)**：
        2026-09-18 的 bug 就是"改判写进了 ``access_overrides``，而事件日志读的是
        原始 ``known_by``"，于是玩家改判私密后日志上永远是【公开】——引擎侧
        其实已经收紧了可见性，只是界面上看不见，看起来就像改判完全没生效。
        ``None`` = 公开（沿用落库时的公开语义），``[]`` = 私密且无人知情。
        """
        return self.save.access_overrides.get(event["id"], event.get("known_by"))

    def access_view(self) -> list[dict[str, Any]]:
        """事件日志的**对外视图**：把改判结果并进 ``known_by``，前端直接读。

        返回的是**副本**——正文与原始 ``known_by`` 是史实，绝不被这里改写
        （改判改的是"访问状态"这一运行态，与状态撤销同一条纪律）。

        ``access_rejudged`` 让"改判"这个**动作**本身也看得见：否则界面上分不清
        一条私密事件是当初就私密、还是玩家事后改判的。判据 = **当前生效的名单
        来自改判**（override 是个名单）；"改回公开"（override 置 ``None``）不标
        ——那时状态与原本的「公开」完全一致，标上只是噪声。与 ``state_view`` 同源
        纪律：可改的字段必须在清单里看得见，而"看得见"的版本由引擎侧拼好，
        前端不做二次计算（那是第二份权威）。
        """
        return [
            {
                **event,
                "known_by": self.effective_known_by(event),
                "access_rejudged": self.save.access_overrides.get(event["id"]) is not None,
            }
            for event in self.narratives
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def allocate_event_id(self) -> str:
        event_id = f"ev_{self.save.meta.next_event_id:05d}"
        self.save.meta.next_event_id += 1
        return event_id

    def append(self, event: dict[str, Any], *, flush: bool = True) -> None:
        """Add one event to the stream and indexes.

        flush=True (default) writes through to events.jsonl immediately.
        flush=False keeps the write in memory only — used by turn commit so
        all of a turn's events hit disk together with save.json (one commit
        unit); call flush_events() at the end of the commit.
        """
        self.events.append(event)
        self._index(event)
        if flush:
            append_event(self.events_path, event)
        else:
            self._pending_events.append(event)

    def flush_events(self) -> None:
        """Write deferred events to events.jsonl (commit tail)."""
        while self._pending_events:
            append_event(self.events_path, self._pending_events.pop(0))

    def persist_save(self) -> None:
        write_json_atomic(self.save_path, self.save.model_dump())

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def _probe(self, entity: str) -> dict[str, Any] | None:
        """Look up the location snapshot, counting index hits/misses."""
        event = self.last_location.get(entity)
        if event is not None:
            self.cache_stats["hits"] += 1
        else:
            self.cache_stats["misses"] += 1
        return event

    def where_is(self, entity: str) -> dict[str, Any] | None:
        return self._probe(entity)

    def player_name(self) -> str:
        """主角名（= 人物表键 = 事件日志里记的名字）。"""
        return self.world.player_name()

    def current_scene(self) -> str:
        """当前场景（镜头位置）：存档小抄优先，空则回开局场景。"""
        return self.save.player_scene or self.world.start_scene_id()

    def present_at(self, scene: str) -> list[str]:
        """在场者 = 场景里的**有档案或有键**的角色。

        域 = 人物表 ∪ 未落卡的确定人物（``save.unfiled``，有键无卡，2026-09-19）。
        后者没有人物卡，但引擎已经认定他是个"人"（有名字、与玩家有往来 ≥2 轮），
        他出现在场景里就该被看见、被注入。**没有名字的即兴角色永远不在这个域里**
        ——它们从不进 ``participants``，``last_location`` 里也就没有它们。
        """
        domain = list(self.world.npcs)
        for name in self.save.unfiled:
            if name and name not in self.world.npcs and name not in domain:
                domain.append(name)
        result = []
        for subject in domain:
            event = self._probe(subject)
            if event is None:
                continue
            entity = self.save.entities.get(subject)
            if entity and entity.lifecycle == "retired":
                continue  # 退场者停止参与在场推导与主动调度（REQ 〇章）
            if event.get("location") == scene:
                result.append(subject)
        return sorted(result)

    def cache_stats_snapshot(self) -> dict[str, int]:
        return dict(self.cache_stats)

    def note_location_candidate(self, scene_id: str, *aliases: str) -> bool:
        """记一条"建议落卡"的地点候选（落卡窗口 2.0，2026-09-19）。

        审计判定"玩家要去 / 回访 / 会复用"时调这里，**不再直接写 ``scenes.json``**
        ——场景是资产，只有玩家能在落卡窗口拍板。写资产的唯一入口是
        ``POST /sessions/{sid}/landing/scene/{name}/file``（它走
        ``check_assets`` + ``save_world_assets`` 那条资产写路径）。

        三种情形：
        - 已注册（``world.scenes`` 里有）→ 什么都不做。玩家可能先前在窗口里拒绝过、
          后来又在工作台手动加了，此时不该再打扰他。
        - 已在册且 ``dismissed`` → **只追加别名，不改 status**。× 是玩家的裁决，
          审计无权翻案；但别名还得攒（否则变体名下一轮又被当新地点报一次）。
        - 否则 upsert：``status=pending``，别名累积。

        ``aliases`` 是历轮审计给的变体名（``AuditOutput.scene_name``）。累积不是
        锦上添花：解析域（``_resolve_scene`` / ``resolve_destination``）只认已注册
        场景 ∪ 本册，缺了它"巷子深处的旧楼"会与"老巷旧楼"排成两条。
        """
        scene_id = (scene_id or "").strip()
        if not scene_id or any(s.id == scene_id for s in self.world.scenes):
            return False
        item = self.save.locations.get(scene_id)
        if item is None:
            item = LocationCandidate(first_seen=self.save.clock or "")
            self.save.locations[scene_id] = item
        # id 自己不必进别名表（它已经是键）；空串与重复名不收——审计把 location
        # 与 scene_name 填成同一个中文名是常态（提示词就是这么要求的），不是异常。
        for alias in aliases:
            alias = (alias or "").strip()
            if alias and alias != scene_id and alias not in item.aliases:
                item.aliases.append(alias)
        return True

    def location_aliases(self) -> dict[str, str]:
        """候选册的「叫法 → 正名」映射（含每个条目的正名自己）。

        给解析域用：``_resolve_scene``（纠偏）与 ``resolve_destination``（寻路）
        都必须认得**还没落卡**的地点，否则玩家在窗口处理之前，"去老巷旧楼"既走
        不到确定性寻路，同一个地点还会在队列里排成同义重复的两条。
        """
        out: dict[str, str] = {}
        for name, item in self.save.locations.items():
            out[name] = name
            for alias in item.aliases:
                out.setdefault(alias, name)
        return out

    def pending_locations(self) -> list[str]:
        """待落卡的地点（窗口列出来的那些，按首次出现排序稳定）。"""
        return [n for n, item in self.save.locations.items() if item.status == "pending"]

    def visible_to(self, viewer: str) -> list[dict[str, Any]]:
        """Narrative events visible to a viewer.

        Public events are visible to everyone; private events require the
        viewer to be in known_by. Access overrides in save.json take
        precedence over the original event field.
        """
        result = []
        for event in self.narratives:
            known_by = self.effective_known_by(event)
            if known_by is None or viewer in known_by:
                result.append(event)
        return result

    def _scene_region(self, scene_id: str) -> str | None:
        """读取场景的 region（消息域）；空 = 全域公共区（None）."""
        scene = next((s for s in self.world.scenes if s.id == scene_id), None)
        if scene is None:
            return None
        return scene.region.strip() or None

    def _event_region(self, event: dict[str, Any]) -> str | None:
        """事件发生地归属：注册场景取其 region（消息域，空 = 全域公共区）；
        location 指向场景表查不到的一次性布景 → 哨兵 `adhoc`（不进任何听域的
        风闻通道——宁可不闻，不错闻；在场者走亲历通道不受影响）。"""
        loc = event.get("location") or ""
        if not loc:
            return None
        scene = next((s for s in self.world.scenes if s.id == loc), None)
        if scene is None:
            return ADHOC_REGION
        return scene.region.strip() or None

    def _npc_hearing_regions(self, npc_id: str) -> set[str]:
        """NPC 的听域（公开事件的地域耳闻范围）。

        优先取人物卡的 region 消息域（来属地/听域，作者/转正时声明——出差外地人
        卡上打原属地，就不该耳闻本地旧事）；卡上为空 → 按亲历事件的发生地
        region 推导（本地老 NPC 天然正确；跟玩家旅行过的 NPC 听域随之扩大）；
        推导为空（刚出场的无历史 NPC）→ 听域为空，保守地什么区域的公开旧事
        都不闻（宁可不闻，不错闻）。哨兵 `adhoc` 永不进听域——一次性布景的
        公开事件对任何人不风闻。
        """
        npc = self.world.npcs.get(npc_id)
        card_regions: set[str] = set()
        if npc is not None:
            card_regions = {
                r.strip() for r in (getattr(npc, "region", None) or []) if r.strip() and r.strip() != ADHOC_REGION
            }
        if card_regions:
            return card_regions
        derived: set[str] = set()
        for ev in self.by_participant.get(npc_id, []):
            r = self._event_region(ev)
            if r is not None and r != ADHOC_REGION:
                derived.add(r)
        return derived

    def known_set(self, npc_id: str, scene_id: str, limit: int) -> list[str]:
        """Mechanical knowledge boundary for one NPC (装配层单一事实源).

        ``limit``：取最近几条，**0 = 全部**（2026-09-16 反转旧口径"0 = 不注入"
        ——旧语义与玩家的直觉相反，也与事件日志的 0 不一致）。**不留默认值**：
        唯一权威是系统设置 ``Settings.limits()``，签名里再写一个数字就是隐患。

        取代旧 ``experiences()``（2026-09-16 删除：app 内零调用点，仅单测在跑）。

        已知集 = 亲历（全量 by_participant，不随滑窗） ∪ known_by 含己（含改判）
        ∪ 听域内公开事件。听域取自人物卡 `region` 字段（来属地/听域），缺省按
        亲历事件发生地推导（`_npc_hearing_regions`）。场景 region 为空 =
        全域公共区（单区域内容包行为与旧口径兼容）：**无地域归属的公开事件人人
        可闻，与听域无关**；听域空集只掐掉"带地域归属的公开事件"这一路。
        按事件时间排序，取最近 limit 条。

        注：`scene_id` 为兼容装配层调用签名保留——听域口径落地后，筛选已改为
        按 NPC 听域而非"当前场景 region"，该参数不再参与计算。
        """
        hearing = self._npc_hearing_regions(npc_id)
        seen: set[str] = set()
        picked: list[dict[str, Any]] = []
        for ev in self.by_participant.get(npc_id, []) + self.by_knower.get(npc_id, []):
            ev_id = ev["id"]
            if ev_id in seen:
                continue
            seen.add(ev_id)
            known_by = self.effective_known_by(ev)
            if known_by is not None and npc_id not in known_by:
                continue
            picked.append(ev)
        for ev in self.narratives:
            ev_id = ev["id"]
            if ev_id in seen:
                continue
            known_by = self.effective_known_by(ev)
            if known_by is not None:
                continue  # 已知集第三来源只收公开事件
            ev_region = self._event_region(ev)
            # 全域公共区（无标签）人人可闻；有归属的事件须落在该 NPC 听域内。
            # 注意用听域而非当前场景 region——刚出差到本地的外乡人不应耳闻本地旧事。
            if ev_region is not None and ev_region not in hearing:
                continue
            seen.add(ev_id)
            picked.append(ev)
        picked.sort(key=lambda e: e.get("at", ""))
        lines = [f"{e.get('at', '')} {e.get('summary') or (e.get('body') or '')[:40]}" for e in picked]
        # 0 = 全部（2026-09-16 反转旧口径）。必须显式判断：`[-0:]` 恰好也等于
        # "取全部"，但那是巧合，不该靠它表达意图；负数同理会砍掉尾部若干条。
        return lines if limit <= 0 else lines[-limit:]