from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.store import new_id, write_json_atomic
from app.core.workorder import STATE_ADD_PER_CHAR, STATE_TEXT_MAX
from app.ledger.queries import Ledger
from app.ledger.save import EntityRuntime, StateItem


# 单回合审计估时长的保险丝（分钟）：审计偶尔把"聊天里提到时间"当成时间流逝，
# 不截断会静默漂移。24 小时——睡觉（480）过半，"睡两天"这类要被截到一天。
AUDIT_DELTA_CAP_MINUTES = 1440


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
    delta_minutes: int = 0  # 旧字段：只剩磁盘上的历史候选还在用（规则侧已改走 settle_to）
    # 规则侧的跳时意图：**绝对时刻**（ISO）。产出时刻而不是时长，是为了让"次日"
    # 这类意图不被当前钟点污染，也从结构上避免与审计估时长叠加（2026-09-14）。
    settle_to: str = ""
    settle_label: str = ""  # 人话标签，供提示词与诊断
    narrative: dict | None = None
    events: list[dict] = Field(default_factory=list)
    axes: dict[str, int] = Field(default_factory=dict)  # 二期预留
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
            except Exception:
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
            except Exception:
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
            except Exception:
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

        规则侧（候选落盘的 pre-solve）：移动目的地 + 跳时的**绝对时刻**
        （``side_effects.settle_to``）。正文语义副作用（时间/在场/私密、场景注册、
        目标、生命周期、角色状态）来自审计推断——审计在采纳这一刻作为**唯一结算点**运行，
        它自己什么都不写，由本方法落地。

        时间结算走一条五档优先级链（见下方注释），delta 只可能被加一次；整条链
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

        # World clock：结算优先级链（2026-09-14 重写 + 加规则对钟档）。
        #
        # ① 审计 clock_to（正文明确到点）
        # ② 规则 settle_to（玩家明示意图 → 绝对时刻；审计沉默/失败时的确定性来源）
        # ③ 审计 delta（对"实际跨了多久"的观测，clamp 进 [0, 1440]）
        # ④ 规则 delta（只剩磁盘上的旧候选还有这个字段）
        # ⑤ 0
        #
        # delta **只可能被加一次**。原实现是"规则 delta + audit delta"相加，而审计
        # 工单不接收 rule_bundle（它不知道规则已推过时间）→ 系统性多算：实测
        # "第二天去学校"多算 5.4h（规则 +12h、审计估 22.6h 被保险丝截到 16h，相加 28h
        # → 次日 13:25，而正文说的是早上）。另修两处：负数 delta 会让时钟倒流；
        # clock_to 无方向守卫时，审计把日期填错能把时钟拉回过去。
        from app.runtime.session import world_start_time

        clock_before = self.ledger.save.clock or world_start_time(self.ledger.world)
        clock = clock_before
        rule_settle = candidate.side_effects.settle_to or ""
        delta_rule = max(0, candidate.side_effects.delta_minutes or 0)
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
        target_src = ""
        settlement: dict = {
            "turn_id": candidate.turn_id,
            "candidate_id": candidate.candidate_id,
            "clock_before": clock_before,
            "clock_after": clock_before,
            "source": "none",  # clock_to | clock_to_rule | audit | rule | none
            "settle_rule": rule_settle or None,
            "settle_rule_label": candidate.side_effects.settle_label or None,
            "settle_rule_rejected": None,
            "delta_rule": delta_rule,
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
                target_src = "clock_to"
        if target is None and rule_settle:
            target, settlement["settle_rule_rejected"] = _accept(rule_settle)
            if target is not None:
                target_src = "clock_to_rule"

        if target is not None:
            # 对钟涵盖估时长 → delta 全部丢弃（不是相加）。
            clock = target.isoformat()
            settlement["source"] = target_src
        else:
            # delta 只加一次：审计（对"实际跨了多久"的观测）优先，规则 delta 兜底。
            delta = delta_audit or delta_rule
            settlement["source"] = "audit" if delta_audit else ("rule" if delta_rule else "none")
            if delta:
                if base is None:
                    settlement["error"] = f"当前时钟无法解析，未推进：{clock_before!r}"
                else:
                    clock = (base + dt.timedelta(minutes=delta)).replace(microsecond=0).isoformat()
                    settlement["delta_applied"] = delta

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
            """场景键纠偏：键命中注册场景直接用；否则按中文名/别名反查
            （审计偶尔自造变体名；scene_name 通常恰是注册场景名，可机械救回）。
            都未命中 → 原样（一次性布景）。"""
            if loc and loc in {s.id for s in self.ledger.world.scenes}:
                return loc
            for alias in aliases:
                if not alias:
                    continue
                for s in self.ledger.world.scenes:
                    if alias == s.id or alias in s.aliases:
                        return s.id
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
                self.ledger.register_scene(location)
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

        # v1 relation axes are reserved; just copy values if provided.
        for key, value in candidate.side_effects.axes.items():
            self.ledger.save.axes[key] = value

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
                {"npc_id": npc_id, "id": item.id, "text": item.text, "event_id": event_id}
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
            item = self.ledger.save.entities[npc_id].states.pop(index)
            event_id = self.ledger.allocate_event_id()
            self._append_state_event(
                event_id, npc_id, clock, f"{npc_id}状态结束：{item.text}"
            )
            change["removed"].append(
                {"npc_id": npc_id, "id": item.id, "text": item.text, "event_id": event_id}
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
                {"npc_id": npc_id, "id": item.id, "text": item.text, "event_id": event_id}
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
                }
            )
        # 与采纳一样"先流水后存档"：存档是提交标记，崩在中间也不会丢记账事件。
        self.ledger.flush_events()
        self.ledger.persist_save()
        return True