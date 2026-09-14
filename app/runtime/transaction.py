from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.store import new_id, write_json_atomic
from app.ledger.queries import Ledger


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
        目标、生命周期）来自审计推断——审计在采纳这一刻作为**唯一结算点**运行，
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
                    from app.ledger.save import EntityRuntime

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