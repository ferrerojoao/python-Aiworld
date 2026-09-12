from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.store import new_id, write_json_atomic
from app.ledger.queries import Ledger


class SideEffects(BaseModel):
    delta_minutes: int = 0
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

        Rule-solved effects (move/jump delta, destination) come from the
        candidate; prose-semantic effects (time/location/presence/privacy,
        scene registration, goals, lifecycle) come from the audit inference
        — the audit runs here as the single settlement point and writes
        nothing itself; this method applies its result.
        """
        candidate = self.candidates.load(candidate_id)
        if candidate is None:
            raise KeyError(f"candidate not found: {candidate_id}")

        audit_out = None
        if audit is not None:
            try:
                audit_out = await audit(candidate)
            except Exception as exc:  # noqa: BLE001
                # Audit failure must not roll back the adopt nor stay silent.
                self.ledger.save.audit_last_error = f"{type(exc).__name__}: {exc}"

        # World clock: rule delta (move/jump) + prose-semantic delta.
        from app.runtime.session import world_start_time

        clock = self.ledger.save.clock or world_start_time(self.ledger.world)
        delta = candidate.side_effects.delta_minutes or 0
        if audit_out is not None:
            # 保险丝：审计估时长抽风（如把聊天提时间当流逝）时截断，防静默漂移；
            # 规则 delta（move/jump 玩家意图）不受限。单回合审计上限 = 16 小时。
            delta += min(audit_out.delta_minutes or 0, 960)
        # 正文明确说了故事时间走到几点 → 绝对对钟优先于估时长（时长已被对钟涵盖）。
        if audit_out is not None and audit_out.clock_to:
            try:
                clock = dt.datetime.fromisoformat(audit_out.clock_to).replace(microsecond=0).isoformat()
                delta = 0
            except ValueError:
                pass  # 格式坏 → 退回估时长，不当致命错误
        if delta:
            try:
                parsed = dt.datetime.fromisoformat(clock)
                parsed += dt.timedelta(minutes=delta)
                clock = parsed.replace(microsecond=0).isoformat()
            except ValueError:
                pass
        self.ledger.save.clock = clock

        # Narrative record: rule-solved location wins; audit fills the rest.
        narrated = candidate.side_effects.narrative or {}
        location = narrated.get("location") or (audit_out.location if audit_out else None) or self.ledger.save.player_scene
        participants = (audit_out.participants if audit_out and audit_out.participants else None) or ["player"]
        # 玩家是每条事件的产生者，恒在参与者名单（审计漏报时兜底）。
        participants = sorted(set(participants) | {"player"})
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
        for mv in (audit_out.npc_moves if audit_out else []):
            mv = mv or {}
            npc_id = mv.get("npc_id") or ""
            dest = mv.get("location") or ""
            if not dest or npc_id == "player" or npc_id not in self.ledger.world.npcs:
                continue
            dest = _resolve_scene(dest, mv.get("scene_name") or "")
            self.ledger.append(
                {
                    "id": self.ledger.allocate_event_id(),
                    "kind": "narrative",
                    "at": clock,
                    "location": dest,
                    "participants": [npc_id],
                    "known_by": sorted({npc_id, "player"}),
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
                            "known_by": sorted({npc_id, "player"}),
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