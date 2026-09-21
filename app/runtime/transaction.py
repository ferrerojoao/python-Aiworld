from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.store import write_json_atomic
from app.core.workorder import STATE_TEXT_MAX
from app.ledger.queries import Ledger
from app.ledger.save import EntityRuntime, StateItem

# 单回合审计估时长的保险丝（分钟）：审计偶尔把"聊天里提到时间"当成时间流逝，
# 不截断会静默漂移。24 小时——睡觉（480）过半，"睡两天"这类要被截到一天。
AUDIT_DELTA_CAP_MINUTES = 1440

# NPC 落卡机制（2026-09-19）：一个**有名字**的角色与玩家有往来累计到这么多轮，
# 引擎就认他为"确定人物"并授**键**（进 save.unfiled，等玩家落卡）。
# 键是运行态、卡是资产；没名字的即兴角色永远不计数（审计侧就不收没名字的出场）。
# 2 的来源：一轮擦肩而过说明不了什么，"问了名字还接着互动"才算数（与玩家敲定）。
UNFILED_KEY_THRESHOLD = 2


def _parse_clock(value: str | None) -> dt.datetime | None:
    """宽松解析存档时钟；解析失败返回 None（由调用方决定拒绝还是兜底）。

    带时区的串一律拍成 naive：故事时钟是架空世界的本地墙钟，不参与跨时区
    换算；若不统一，下面 `target < base` 的比较会抛 TypeError（naive vs aware）。
    """
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed.replace(microsecond=0, tzinfo=None)


def _is_expired(until: str, clock: str) -> bool:
    """``until`` 是否已到（<= clock）。任一侧解析失败 → False。

    解析失败一律当作**永不到期**：清理是不可逆动作，"时钟格式歪掉"不该成为
    静默删状态的理由（宁可不清理，不要误删）。
    """
    limit, now = _parse_clock(until), _parse_clock(clock)
    if limit is None or now is None:
        return False
    return limit <= now


def _clean_until(value) -> str:
    """审计报的 ``until``：能解析成日期才采纳，否则当"没给"（存空串）。

    审计偶尔会写人话（"三天后"）或写歪格式，直接存进去的后果是**这条状态永远
    不到期**（`_is_expired` 解析失败一律当永不到期）——一条本该自愈的"病倒"
    就永久粘在提示词里。宁缺勿滥：收不下就当没给，玩家仍可手动撤销。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return text if _parse_clock(text) else ""


class SideEffects(BaseModel):
    """候选的世界副作用。**时间不在这里**——时钟由审计在采纳时独家结算。

    规则侧的 ``settle_to`` / ``settle_label`` / ``delta_minutes`` 已于 2026-09-16
    下线（原委见 ``app/rules/route.py`` 模块注释）。磁盘上的旧候选仍带着这几个键，
    pydantic 默认忽略未知字段，所以不迁移也能读。
    """

    narrative: dict | None = None
    events: list[dict] = Field(default_factory=list)
    overrides: list[dict] = Field(default_factory=list)


class Candidate(BaseModel):
    candidate_id: str
    turn_id: str
    trace_id: str = ""
    mode: str = "initial"
    status: str = "pending"
    player_input: str = ""
    writer_directive: str = ""  # 本回合 ((...)) 导演指令，重掷时沿用
    prose: str = ""
    side_effects: SideEffects = Field(default_factory=SideEffects)
    conflicts: list[dict] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class CandidateStore:
    """File-backed storage for multiple pending candidates per turn.

    Files are named ``<turn_id>_<candidate_id>.candidate.json``.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _iter_paths(self):
        if not self.root.is_dir():
            return
        yield from self.root.glob("*.candidate.json")

    def _path_for(self, candidate: Candidate) -> Path:
        return self.root / f"{candidate.turn_id}_{candidate.candidate_id}.candidate.json"

    def save(self, candidate: Candidate) -> None:
        write_json_atomic(self._path_for(candidate), candidate.model_dump())

    def load(self, candidate_id: str) -> Candidate | None:
        for path in self._iter_paths():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("candidate_id") == candidate_id:
                    return Candidate.model_validate(data)
            except Exception:  # noqa: BLE001 - 候选文件损坏/半写就跳过，别让一个坏文件卡住整轮
                continue
        return None

    def list_pending(self) -> list[Candidate]:
        result: list[Candidate] = []
        for path in self._iter_paths():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                cand = Candidate.model_validate(data)
                if cand.status == "pending":
                    result.append(cand)
            except Exception:  # noqa: BLE001 - 候选文件损坏/半写就跳过，别让一个坏文件卡住整轮
                continue
        return sorted(result, key=lambda c: c.created_at)

    def list_for_turn(self, turn_id: str) -> list[Candidate]:
        return [c for c in self.list_pending() if c.turn_id == turn_id]

    def delete_candidate(self, candidate_id: str) -> None:
        for path in self._iter_paths():
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("candidate_id") == candidate_id:
                    path.unlink()
                    return
            except Exception:  # noqa: BLE001 - 候选文件损坏/半写就跳过，别让一个坏文件卡住整轮
                continue

    def delete_turn(self, turn_id: str) -> None:
        for cand in self.list_for_turn(turn_id):
            self.delete_candidate(cand.candidate_id)

    def delete_all(self) -> None:
        for cand in self.list_pending():
            self.delete_candidate(cand.candidate_id)


class Transaction:
    """Adopt/discard operations for a session's pending candidates."""

    def __init__(self, ledger: Ledger, candidates: CandidateStore):
        self.ledger = ledger
        self.candidates = candidates

    async def commit(self, candidate_id: str, *, audit) -> None:
        """Adopt a candidate.

        规则侧（候选落盘的 pre-solve）只剩**移动目的地**；正文语义副作用
        （时间/在场/私密、场景注册、目标、生命周期、角色状态）来自审计推断——
        审计在采纳这一刻作为**唯一结算点**运行，它自己什么都不写，由本方法落地。

        时间结算走两档优先级链（见下方注释），delta 只可能被加一次；整条链
        的留痕写进 ``save.last_settlement``（P1 可观测性，顶栏时钟 hover 可见）。
        """
        candidate = self.candidates.load(candidate_id)
        if candidate is None:
            raise KeyError(f"candidate not found: {candidate_id}")

        audit_out = None
        audit_error: str | None = None
        if audit is not None:
            try:
                audit_out = await audit(candidate)
            except Exception as exc:  # noqa: BLE001
                # Audit failure must not roll back the adopt nor stay silent.
                audit_error = f"{type(exc).__name__}: {exc}"
                self.ledger.save.audit_last_error = audit_error

        # World clock：两档（2026-09-16 简化）。
        #
        # ① 审计 clock_to（正文或玩家输入明确给出到达时刻）
        # ② 审计 delta_minutes（按剧情实际时长估，clamp 进 [0, 1440]）
        # ③ 0
        #
        # 规则侧的 settle_to / delta_minutes 已下线：那套词表用正则匹配自由语句，
        # 假阳越补越多（"没等到明天就走了""三天后我才明白""直到我有一点累"），
        # 而且它一旦先定钟，就必须再经 settle_hint 通知审计"这一段别再估"——凭空
        # 造出一个跨工位的重复计费风险。现在审计是唯一的时间结算方，它本来就同时
        # 看得到正文与玩家输入。
        #
        # 两道机械守卫保留：clock_to 过方向守卫（早于当前钟 → 拒，防审计把日期填错
        # 把时钟拉回过去），delta 走 max(0, …) 并夹进保险丝。
        from app.runtime.session import world_start_time

        clock_before = self.ledger.save.clock or world_start_time(self.ledger.world)
        clock = clock_before
        raw_audit = audit_out.delta_minutes if audit_out is not None else None
        delta_audit = max(0, min(raw_audit or 0, AUDIT_DELTA_CAP_MINUTES))
        clock_to_audit = (audit_out.clock_to or "") if audit_out is not None else ""

        base = _parse_clock(clock_before)

        def _accept(raw: str) -> tuple[dt.datetime | None, str | None]:
            """解析一个绝对时刻并过方向守卫；返回 (目标, 拒绝原因)。"""
            parsed = _parse_clock(raw)
            if parsed is None:
                return None, "时间格式无法解析"
            if base is not None and parsed < base:
                return None, "早于当前时钟，疑似日期填错"
            return parsed, None

        target = None
        settlement: dict = {
            "turn_id": candidate.turn_id,
            "candidate_id": candidate.candidate_id,
            "clock_before": clock_before,
            "clock_after": clock_before,
            "source": "none",  # clock_to | audit | none
            "delta_audit": delta_audit,
            "delta_audit_raw": raw_audit,
            "delta_applied": 0,
            "audit_capped": raw_audit is not None and raw_audit > AUDIT_DELTA_CAP_MINUTES,
            "audit_negative": raw_audit is not None and raw_audit < 0,
            "clock_to_audit": clock_to_audit or None,
            "clock_to_rejected": None,
            "error": None,
            "audit_error": audit_error,
        }

        if clock_to_audit:
            target, settlement["clock_to_rejected"] = _accept(clock_to_audit)

        if target is not None:
            # 对钟涵盖估时长 → delta 全部丢弃（不是相加）。
            clock = target.isoformat()
            settlement["source"] = "clock_to"
        elif delta_audit:
            settlement["source"] = "audit"
            if base is None:
                settlement["error"] = f"当前时钟无法解析，未推进：{clock_before!r}"
            else:
                clock = (base + dt.timedelta(minutes=delta_audit)).replace(microsecond=0).isoformat()
                settlement["delta_applied"] = delta_audit

        self.ledger.save.clock = clock
        settlement["clock_after"] = clock
        # 结算留痕（P1）：整条优先级链落盘，顶栏时钟 hover 可见。
        self.ledger.save.last_settlement = settlement

        # Narrative record: rule-solved location wins; audit fills the rest.
        narrated = candidate.side_effects.narrative or {}
        player = self.ledger.player_name()
        location = (
            narrated.get("location")
            or (audit_out.location if audit_out else None)
            or self.ledger.current_scene()
        )
        participants = (audit_out.participants if audit_out and audit_out.participants else None) or [player]
        # 主角是每条事件的产生者，恒在参与者名单（审计漏报时兜底）。
        participants = sorted(set(participants) | {player})
        private = bool(audit_out.private) if audit_out else False
        known_by = participants if private else None

        def _resolve_scene(loc: str, *aliases: str) -> str:
            """场景键纠偏：**id 或别名**命中已知地点就用它；否则按中文名/别名反查
            （审计偶尔自造变体名；scene_name 通常恰是场景名，可机械救回）。
            都未命中 → 原样（一次性布景）。

            loc 自己也认别名（2026-09-16 补）：原先第一步只查 ``s.id``，别名仅
            作为第二参数被反查——审计**只给一个叫法**且那是个别名时（qingshi2 实测
            里 ``location_name`` 就是空的），"鱼市"的别名"码头鱼市"会凭空多注册
            一个同义场景。id 仍优先于别名。

            **解析域 = 已注册场景 ∪ 地点候选册**（落卡窗口 2.0，2026-09-19）：
            待落卡 / 已定为临时的地方也要认得出来。不做这一步，玩家还没处理的
            "老巷旧楼"下一轮就会被当成新地点，队列里排成同义重复的两条。
            场景表优先——已注册的才是唯一真相。
            """
            by_id = {s.id: s.id for s in self.ledger.world.scenes}
            by_alias: dict[str, str] = {}
            for scene in self.ledger.world.scenes:
                for name in scene.aliases:
                    by_alias.setdefault(name, scene.id)
            for call, canonical in self.ledger.location_aliases().items():
                if call in by_id or call in by_alias:
                    continue
                by_alias.setdefault(call, canonical)
            if loc:
                if loc in by_id:
                    return by_id[loc]
                if loc in by_alias:
                    return by_alias[loc]
            for alias in aliases:
                if not alias:
                    continue
                if alias in by_id:
                    return by_id[alias]
                if alias in by_alias:
                    return by_alias[alias]
            return loc

        audit_alias = audit_out.scene_name if audit_out else ""
        location = _resolve_scene(
            location, audit_alias, (narrated.get("location_name") or "")
        )

        event_id = self.ledger.allocate_event_id()
        narrative = {
            "id": event_id,
            "kind": "narrative",
            "at": clock,
            "location": location,
            "participants": participants,
            "known_by": known_by,
            "body": candidate.prose,
            "summary": narrated.get("summary") or (candidate.prose or "")[:40],
            "player_input": candidate.player_input or None,
            "source": "turn",
        }
        if location not in {s.id for s in self.ledger.world.scenes}:
            scene_alias = narrated.get("location_name")
            if audit_out and audit_out.scene_name and not scene_alias:
                scene_alias = audit_out.scene_name
            if scene_alias:
                narrative["location_name"] = scene_alias
            if audit_out and audit_out.register_scene and location:
                # 审计只**建议**落卡（落卡窗口 2.0，2026-09-19）。场景是**资产**，
                # 不再由审计单方面写进 scenes.json——那会凭空多出一个描述只能是
                # 占位句（"暂无描述。"）的场景，而那句每轮都被注入，没人被通知去补。
                # 现在只记候选册，等玩家在落卡窗口过目（与人物侧"引擎只给键、
                # 卡必须玩家落"同构）。变体名（scene_alias）一并攒下：同一个地点换个
                # 叫法下轮才救得回来（否则"老巷旧楼"落表后，"巷子深处的旧楼"会被
                # 当成新地点再排一条）。2026-09-16 起攒别名，2026-09-19 改为入册。
                self.ledger.note_location_candidate(location, scene_alias or "")
        self.ledger.append(narrative, flush=False)
        self.ledger.save.player_scene = location

        for event in candidate.side_effects.events:
            event = dict(event)
            event.setdefault("id", self.ledger.allocate_event_id())
            event.setdefault("kind", "narrative")
            event.setdefault("at", clock)
            self.ledger.append(event, flush=False)

        # NPC 离场落账：正文明确写出某人离开去了别处 → 轻量位置事件，快照随之更新。
        # （离场者不是主事件的参与者之外的更新渠道——快照只能被事件改变；去向未注册
        # 也无妨：present_at 永远匹配不上它，即"不在任何已注册场景"的机械表达。）
        # 主角不走这条：主角的移动就是主事件本身，被记进 npc_moves 说明审计写反了。
        for mv in (audit_out.npc_moves if audit_out else []):
            mv = mv or {}
            npc_id = mv.get("npc_id") or ""
            dest = mv.get("location") or ""
            if not dest or npc_id == player or npc_id not in self.ledger.world.npcs:
                continue
            dest = _resolve_scene(dest, mv.get("scene_name") or "")
            self.ledger.append(
                {
                    "id": self.ledger.allocate_event_id(),
                    "kind": "narrative",
                    "at": clock,
                    "location": dest,
                    "participants": [npc_id],
                    "known_by": sorted({npc_id, player}),
                    "body": "",
                    "summary": f"{npc_id}前往{dest}",
                    "source": "turn",
                },
                flush=False,
            )

        # Apply the audit's goal/lifecycle settlement.
        if audit_out is not None:
            from app.ledger.goals import complete_goals

            complete_goals(self.ledger, audit_out.completed_goal_ids)
            for item in audit_out.lifecycle or []:
                npc_id = str(item.get("npc_id") or item.get("id") or "")
                status = str(item.get("status") or "")
                # 主角不可退场（2026-09-13）：主角已是人物表里的一员，若不排除，
                # 审计一句"她离开了"就能把主角从世界里注销。
                if npc_id == player:
                    continue
                if npc_id in self.ledger.world.npcs and status == "retired":
                    entity = self.ledger.save.entities.setdefault(npc_id, EntityRuntime())
                    if entity.lifecycle == "retired":
                        continue  # 已退场不重复留痕
                    entity.lifecycle = "retired"
                    # 退场留痕（2026-09-12）：与 npc_moves/导演 retire 同口径——
                    # 事件日志是长期记忆比对基准，静默退场会让"他不在了"无史实可引。
                    self.ledger.append(
                        {
                            "id": self.ledger.allocate_event_id(),
                            "kind": "narrative",
                            "at": clock,
                            "location": None,
                            "participants": [npc_id],
                            "known_by": sorted({npc_id, player}),
                            "body": "",
                            "summary": f"{npc_id}退场（永久离开舞台）",
                            "player_input": None,
                            "source": "turn",
                        },
                        flush=False,
                    )

        # NPC 落卡机制（2026-09-19）：累计出场、授键。放在目标/退场结算之后——
        # 退场者不该再被授键，所以要先让 lifecycle 落地。
        self._apply_featured(audit_out, player)

        # 角色状态（REQ 〇章；Step 2，2026-09-14）：到期清算 + 审计提议的增删。
        # 与时间结算一样是"采纳这一刻"的一次性动作，整批结果留痕在
        # save.last_state_change（长期事实误加的代价高，必须查得出原因）。
        self._apply_state_changes(candidate, audit_out, clock)

        # World book: adopt rebuilds the active list from the adopted prose
        # (采纳阶段：清空 → 命中已采纳正文 → 列表 = 上轮事实优先)。
        from app.rules.lorebook import rebuild_active_lore

        self.ledger.save.active_lore_ids = rebuild_active_lore(
            self.ledger.world, candidate.prose
        )

        # 一次提交落盘：本回合事件攒批与存档背靠背写入（先流水后存档，
        # 存档为提交标记）。若仍崩在两笔之间，重开时 _calibrate_event_counter
        # 按流水校准计数器，事件号不复用。
        self.ledger.flush_events()
        self.ledger.persist_save()
        # Clean this turn's candidate files only after a successful commit.
        self.candidates.delete_turn(candidate.turn_id)

    def discard_turn(self, turn_id: str) -> None:
        self.candidates.delete_turn(turn_id)

    # ------------------------------------------------------------------
    # NPC 落卡机制（2026-09-19）
    # ------------------------------------------------------------------

    def _apply_featured(self, audit_out, player: str) -> None:
        """累计"出场"轮数；到阈值即授**键**（进 ``save.unfiled``）。

        边界（与玩家敲定，唯一口径）：**有名字 + 与玩家有往来累计 ≥2 轮** →
        确定人物。没名字的即兴角色（酒保 / 伙计）审计侧就不收，这里天然不计数
        ——"没名字就无从落卡"，所以它们永远只是即兴角色。

        键与卡分层：**键是运行态**（这里，随世界重置清零），**卡是资产**
        （``npcs/*.json``）。有键无卡 = 未落卡，等玩家确认落卡。

        计数**累计**（不要求连续），一旦授键就保持——直到落卡（``land_card``
        把名字从两处移除）或玩家撤销。已落卡（人物表里有卡）或已退场的不再进
        ``unfiled``：前者已有档案，后者不该再被授键。
        """
        if audit_out is None:
            return
        save = self.ledger.save
        for name in audit_out.featured or []:
            name = str(name or "").strip()
            if not name or name == player:
                continue  # 主角自己不算"出场角色"
            if name in self.ledger.world.npcs:
                save.featured_counts.pop(name, None)
                if name in save.unfiled:  # 中途补了卡 → 键作废
                    save.unfiled.remove(name)
                continue
            entity = save.entities.get(name)
            if entity and entity.lifecycle == "retired":
                save.featured_counts.pop(name, None)
                if name in save.unfiled:
                    save.unfiled.remove(name)
                continue
            save.featured_counts[name] = save.featured_counts.get(name, 0) + 1
            if save.featured_counts[name] >= UNFILED_KEY_THRESHOLD and name not in save.unfiled:
                save.unfiled.append(name)

    # ------------------------------------------------------------------
    # 角色状态 · 长期事实（REQ 〇章；Step 2，2026-09-14）
    # ------------------------------------------------------------------

    def _append_state_event(self, event_id: str, npc_id: str, clock: str, summary: str) -> None:
        """状态变更的记账事件（不是叙述）：body 空、无位置、只留源起。

        与「导演静默覆写只描述位置、不承载情节」同一条纪律——**"谁看见了这个表现"
        由正文那场戏承担**（已落账），这里只负责让状态变化在事件流里留下时间线，
        并给 ``source_event`` 一个可指的对象。形状照 ``retire`` 留痕事件：
        ``participants`` 只有本人（他是这条史实的主体），``known_by`` 含玩家
        （结算范围 = 玩家可感知，所以玩家必然知情）。``location`` 刻意留 None：
        不给它位置语义，免得污染 ``where_is`` 的位置推导。
        """
        self.ledger.append(
            {
                "id": event_id,
                "kind": "narrative",
                "at": clock,
                "location": None,
                "participants": [npc_id],
                "known_by": sorted({npc_id, self.ledger.player_name()}),
                "body": "",
                "summary": summary,
                "player_input": None,
                "source": "state",
            },
            flush=False,
        )

    def _find_state(self, state_id: str) -> tuple[str, int] | None:
        """按 id 找状态（跨角色）：返回 (角色名, 下标)。找不到返回 None。"""
        for npc_id, runtime in self.ledger.save.entities.items():
            for index, item in enumerate(runtime.states):
                if item.id == state_id:
                    return npc_id, index
        return None

    def _expire_states(self, clock: str, expired: list[dict]) -> None:
        """到期清算（规则侧，先于审计变更跑）：``until`` 已到的条目**标记失效**。

        **不删条目**（2026-09-14 晚改，用户拍板）：只写 ``expired_at`` + 落一条
        记账事件。理由——``until`` 是**审计单方面**给的（玩家没有异议渠道），而
        旧行为（到点 pop 掉）会让"审计给了 7 天期限"变成一条玩家事后既查不到、
        也撤不掉的静默删除。现在条目留在存档里：注入侧按 ``expired_at`` 过滤
        （编剧/审计读不到），面板上灰显「已到期」并保留撤销入口——玩家随时能清掉。

        ``expired_at`` 兼作**已记账**的哨兵：否则每轮都会重新越过 ``until``、
        每轮再写一条"状态结束"事件。所以这个守卫是必须的，不是优化。
        """
        for npc_id, runtime in self.ledger.save.entities.items():
            for item in runtime.states:
                if item.expired_at or not item.until:
                    continue
                if not _is_expired(item.until, clock):
                    continue
                item.expired_at = clock
                event_id = self.ledger.allocate_event_id()
                self._append_state_event(
                    event_id, npc_id, clock, f"{npc_id}状态结束：{item.text}"
                )
                expired.append(
                    {
                        "npc_id": npc_id,
                        "id": item.id,
                        "text": item.text,
                        "event_id": event_id,
                        "public": item.public,
                    }
                )

    def _apply_state_changes(self, candidate: Candidate, audit_out, clock: str) -> dict:
        """落地审计提议的角色状态增删（Step 2），并把整批结果写进 last_state_change。

        顺序：① 到期清算（规则）→ ② 新增 → ③ 移除。

        守卫只管**格式与归属**——``npc_id`` 必须在人物表、``text`` 非空、同角色去重
        （否则审计每打一架就把"他刀枪不入"再报一遍，六格很快被同一个事实吃光）、
        同角色本回合最多 ``STATE_ADD_PER_CHAR`` 条（防"给同一个人一口气编一串"）。
        语义上的过度发挥由提示词纪律（"只从正文读到"）与玩家事后撤销兜底。

        **审计失败（``audit_out is None``）→ 状态一动不动**：不能因为审计炸了就
        丢玩家的状态；但到期清算仍照跑（那是规则，不依赖审计）。
        """
        # 每个桶的条目都带 ``public``（2026-09-16 加）：前端「本回合变化」据此挂
        # 「秘」——左栏与状态面板必须**同词**（同一个概念换一种说法，玩家就得重新
        # 猜一遍这两个是不是一回事）。缺字段时前端按"不挂"处理，不会误标。
        change: dict = {
            "turn_id": candidate.turn_id,
            "at": clock,
            "added": [],
            "removed": [],
            "expired": [],
            "skipped": [],
            # 玩家撤销（Step 2b）也记在同一份留痕里：前端靠它对上"这一行的 id
            # 已被撤过"从而原地标灰，而不是让行凭空消失。
            "revoked": [],
        }
        self._expire_states(clock, change["expired"])

        if audit_out is None:
            self.ledger.save.last_state_change = change
            return change

        seen: set[str] = set()
        for raw in audit_out.state_add or []:
            entry = raw if isinstance(raw, dict) else {}
            npc_id = str(entry.get("npc_id") or entry.get("id") or "").strip()
            text = str(entry.get("text") or entry.get("state") or "").strip()
            if not text:
                continue
            if npc_id not in self.ledger.world.npcs:
                change["skipped"].append(
                    {"npc_id": npc_id, "text": text, "reason": "不在人物表"}
                )
                continue
            if npc_id in seen:
                change["skipped"].append(
                    {"npc_id": npc_id, "text": text, "reason": "同一角色本回合已新增一条"}
                )
                continue
            runtime = self.ledger.save.entities.setdefault(npc_id, EntityRuntime())
            # 只与**未失效**的条目比：一条已到期的"病倒"不该永久挡着下一次"病倒"
            # （否则改掉"到期即删除"之后，这个去重会变成新的假阳性来源）。
            if any(
                not it.expired_at and (it.text or "").strip() == text
                for it in runtime.states
            ):
                change["skipped"].append(
                    {"npc_id": npc_id, "text": text, "reason": "已有同一状态"}
                )
                continue
            seen.add(npc_id)
            # 事件号先于条目落定：source_event 必须指得着（可追溯 → 可撤销）。
            event_id = self.ledger.allocate_event_id()
            item = StateItem(
                text=text[:STATE_TEXT_MAX],
                since=clock,
                source_event=event_id,
                public=bool(entry.get("public")),
                # 有期限的状态（"病倒三天"）由审计给出 until，规则侧到点自动清算；
                # 收不下的写法当没给（见 _clean_until）。
                until=_clean_until(entry.get("until")),
            )
            runtime.states.append(item)
            self._append_state_event(
                event_id, npc_id, clock, f"{npc_id}获得状态：{item.text}"
            )
            change["added"].append(
                {
                    "npc_id": npc_id,
                    "id": item.id,
                    "text": item.text,
                    "event_id": event_id,
                    "public": item.public,
                }
            )

        for raw in audit_out.state_remove or []:
            state_id = (
                raw
                if isinstance(raw, str)
                else str((raw or {}).get("state_id") or (raw or {}).get("id") or "")
            )
            state_id = state_id.strip()
            if not state_id:
                continue
            hit = self._find_state(state_id)
            if hit is None:
                change["skipped"].append({"state_id": state_id, "reason": "找不到该状态 id"})
                continue
            npc_id, index = hit
            item = self.ledger.save.entities[npc_id].states[index]
            # ① 已经宣布它结束过（`expired_at` 是**已处理**哨兵）：同一回合再 pop 一次
            # 会重复落一条「状态结束」记账事件，且直接把条目从存档抹掉——「失效不删除」
            # 是给玩家留撤销入口的，这里删了就撤不成。当成已处理，跳过。
            if item.expired_at:
                change["skipped"].append(
                    {"state_id": state_id, "reason": "已到期（到期清算已处理）"}
                )
                continue
            self.ledger.save.entities[npc_id].states.pop(index)
            event_id = self.ledger.allocate_event_id()
            self._append_state_event(
                event_id, npc_id, clock, f"{npc_id}状态结束：{item.text}"
            )
            change["removed"].append(
                {
                    "npc_id": npc_id,
                    "id": item.id,
                    "text": item.text,
                    "event_id": event_id,
                    "public": item.public,
                }
            )

        self.ledger.save.last_state_change = change
        return change

    def add_state(
        self, npc_id: str, text: str, *, until: str = "", public: bool = False
    ) -> StateItem:
        """玩家手动加一条状态（导演窗口，Step 2c）。

        与审计提议的**关键差别：不做语义门槛**。审计那套"只收长期事实"的判据是
        用来防它在正文里过度发挥的；玩家在导演窗口明确要求加一条，就是已经拍板
        ——此时再拿语义闸拦人很荒谬（"她其实是色盲"看着像前史，但玩家说了算）。
        守卫只管**格式与归属**：``npc_id`` 在人物表、``text`` 非空、≤
        ``STATE_TEXT_MAX``、同角色不重复（只与**未失效**的条目比——一条已到期的
        "病倒"不该永久挡着下一次"病倒"）。

        与 ``revoke_state`` 对称：同样落 ``source:"state"`` 的记账事件（summary 带
        "（玩家设定）"，与"（玩家撤销）"成对——事件流里一眼看出是谁的手笔）、同样
        把结果记进 ``last_state_change.added``（前端左侧栏据此给即时撤销入口，
        "刚加完就能反悔"）。

        Raises ValueError：格式 / 归属不合规（API 层答 400），与 ``add_goal`` 同口径。
        """
        npc_id = (npc_id or "").strip()
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        if npc_id not in self.ledger.world.npcs:
            raise ValueError(f"npc not found: {npc_id}")
        clock = self.ledger.save.clock
        runtime = self.ledger.save.entities.setdefault(npc_id, EntityRuntime())
        clipped = text[:STATE_TEXT_MAX]
        if any(
            not it.expired_at and (it.text or "").strip() == clipped
            for it in runtime.states
        ):
            raise ValueError(f"{npc_id} 已有同一状态：{clipped}")
        # 事件号先于条目落定：source_event 必须指得着（可追溯 → 可撤销）。
        event_id = self.ledger.allocate_event_id()
        item = StateItem(
            text=clipped,
            since=clock,
            source_event=event_id,
            public=bool(public),
            until=_clean_until(until),
        )
        runtime.states.append(item)
        self._append_state_event(
            event_id, npc_id, clock, f"{npc_id}获得状态：{item.text}（玩家设定）"
        )
        change = self.ledger.save.last_state_change
        if isinstance(change, dict):
            change.setdefault("added", []).append(
                {
                    "npc_id": npc_id,
                    "id": item.id,
                    "text": item.text,
                    "event_id": event_id,
                    "public": item.public,
                }
            )
        self.ledger.flush_events()
        self.ledger.persist_save()
        return item

    def revoke_state(self, state_id: str) -> bool:
        """玩家撤销一条状态（Step 2b，2026-09-14）：删条目 + 落一条对冲记账事件。

        **不经审计、也不进审计工单**（2026-09-14 用户拍板）：撤销是玩家对自己世界的
        修正，与"访问状态改判"同类——正文史实不可变，但"编剧此后认为他是什么"是
        运行态，玩家有权纠正。刻意**不做"已撤销清单"注入**：正文里那场戏还在，审计
        再读到就再提议一次，玩家可以再撤一次；不为此在每份工单里常驻一行。

        留痕两处：`source:"state"` 的记账事件（summary 带"（玩家撤销）"，事件流里
        查得到谁在什么时候撤的）+ `last_state_change.revoked` 记一笔（前端把那一行
        原地标灰，而不是让它凭空消失——刚点完行就没了，玩家会怀疑自己点错了）。

        找不到（已被撤过 / 已到期 / 重置过）返回 ``False``，让 API 答 404，
        不做静默成功。
        """
        hit = self._find_state(state_id)
        if hit is None:
            return False
        npc_id, index = hit
        item = self.ledger.save.entities[npc_id].states.pop(index)
        clock = self.ledger.save.clock
        event_id = self.ledger.allocate_event_id()
        self._append_state_event(
            event_id, npc_id, clock, f"{npc_id}状态撤销：{item.text}（玩家撤销）"
        )
        change = self.ledger.save.last_state_change
        if isinstance(change, dict):
            change.setdefault("revoked", []).append(
                {
                    "npc_id": npc_id,
                    "id": item.id,
                    "text": item.text,
                    "event_id": event_id,
                    "public": item.public,
                }
            )
        # 与采纳一样"先流水后存档"：存档是提交标记，崩在中间也不会丢记账事件。
        self.ledger.flush_events()
        self.ledger.persist_save()
        return True

    def revise_state(
        self,
        state_id: str,
        *,
        text: str | None = None,
        until: str | None = None,
        public: bool | None = None,
    ) -> StateItem | None:
        """玩家修订一条状态的字段（Step 2c 补，2026-09-16）：**原地改，不换条目**。

        与"撤销 + 新增"的关键差别：``id`` / ``since`` / ``source_event`` 全部保留
        ——起始时间不丢、成因链不断、"他什么时候起就是这样的"这条信息不会因为改掉
        一个错别字而被重置成"刚刚"。改错了（或撤销）仍然随时可回退。

        参数语义：``None`` = 这一项不动；``until`` 给空串 = 清掉期限；``public`` 只
        认布尔。三项都没给（全 ``None``）或给的值与现值完全相同 → 不落事件、不写
        存档（没有变化就不该在事件流里留下"修订过"的痕迹）。

        守卫只管**格式与归属**（与 ``add_state`` 同口径）：``text`` 非空、≤
        ``STATE_TEXT_MAX``、同角色不重复（只与**其它**未失效条目比——拿自己原来的
        文字再提交一次不算冲突）。

        已失效（``expired_at`` 非空）的条目不接受修订：注入侧本来就读不到它，改文字
        玩家看不到效果；改 ``until`` 更是要么立刻又失效、要么等于偷偷复活一条已经
        记过「状态结束」的史实——两条路都在制造困惑。要它回来请撤销后重加。

        Raises ValueError：格式 / 归属不合规（API 层答 400）。
        Returns None：``state_id`` 找不到（API 层答 404，与 ``revoke_state`` 同口径）。
        """
        hit = self._find_state(state_id)
        if hit is None:
            return None
        npc_id, index = hit
        runtime = self.ledger.save.entities[npc_id]
        item = runtime.states[index]
        if item.expired_at:
            raise ValueError("已到期的状态不可修订，请撤销后重加")
        if text is None and until is None and public is None:
            raise ValueError("text / until / public 至少给一个")

        old_text = item.text or ""
        changes: list[str] = []
        if text is not None:
            new_text = (text or "").strip()[:STATE_TEXT_MAX]
            if not new_text:
                raise ValueError("text is required")
            if new_text != old_text and any(
                not it.expired_at and it.id != item.id and (it.text or "").strip() == new_text
                for it in runtime.states
            ):
                raise ValueError(f"{npc_id} 已有同一状态：{new_text}")
            if new_text != old_text:
                changes.append(f"文字「{old_text}」→「{new_text}」")
                item.text = new_text
        if until is not None:
            new_until = _clean_until(until)
            if new_until != item.until:
                changes.append(f"期限「{item.until or '无'}」→「{new_until or '无'}」")
                item.until = new_until
        if public is not None:
            new_public = bool(public)
            if new_public != item.public:
                # 记账文案与界面标签同词：界面挂「秘」、事件流写「旁人看不出」，就是
                # 逼玩家自己把两种说法对上号。此处「公开 / 秘」即面板那两颗标签。
                before = "公开" if item.public else "秘"
                after = "公开" if new_public else "秘"
                changes.append(f"可见性「{before}」→「{after}」")
                item.public = new_public

        if not changes:
            return item

        clock = self.ledger.save.clock
        event_id = self.ledger.allocate_event_id()
        self._append_state_event(
            event_id, npc_id, clock, f"{npc_id}状态修订：{'、'.join(changes)}（玩家修订）"
        )
        # 与撤销对称：留痕进 last_state_change，前端「本回合变化」据此把那一行显示成
        # 「旧 → 新」。**不改 added/removed**——修订不是增删，混进那两桶会让"这回合
        # 多了几条状态"的计数失真。
        change = self.ledger.save.last_state_change
        if isinstance(change, dict):
            change.setdefault("revised", []).append(
                {
                    "npc_id": npc_id,
                    "id": item.id,
                    "text": item.text,
                    "old_text": old_text,
                    "event_id": event_id,
                    # 修订后的可见性（不是修订前的）：左栏要挂的是"这条现在是不是秘"。
                    "public": item.public,
                }
            )
        # 与采纳一样"先流水后存档"：存档是提交标记，崩在中间也不会丢记账事件。
        self.ledger.flush_events()
        self.ledger.persist_save()
        return item