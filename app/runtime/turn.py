from __future__ import annotations

import datetime as dt
import re

from app.config import Settings
from app.core.store import new_id
from app.rules.lorebook import hit_entry_ids, merge_active_lore
from app.rules.movement import resolve_destination, travel_minutes
from app.rules.route import classify_input
from app.rules.scenes import scene_description
from app.runtime.session import GameSession
from app.runtime.transaction import Candidate, CandidateStore, SideEffects, Transaction
from app.world.models import NarrativePreset
from app.workers.actor import run_actor
from app.workers.auditor import run_audit
from app.workers.qc import build_qc_reference, run_qc
from app.workers.writer import run_writer


class NeedChooseCandidate(Exception):
    """Raised when a new turn is submitted while multiple candidates are pending."""


_OOC_RE = re.compile(r"\(\((.+?)\)\)", re.S)


def _extract_directive(text: str) -> tuple[str, str]:
    """Peel ``((...))`` director directives out of the raw input.

    Directives are per-turn writing requirements for the writer (style,
    emphasis). They never enter the story: the remaining text is what gets
    routed and recorded as the player input; directives ride a separate
    writer message and are stored on the candidate so rerolls keep them.
    """
    parts = [m.strip() for m in _OOC_RE.findall(text) if m.strip()]
    clean = _OOC_RE.sub(" ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    directive = "\n".join(f"- {p}" for p in parts)
    return clean, directive


class TurnRunner:
    def __init__(
        self,
        session: GameSession,
        llm,
        settings: Settings,
        preset: NarrativePreset | None = None,
    ):
        self.session = session
        self.llm = llm
        self.settings = settings
        self.preset = preset or session.world.presets
        self.transaction = Transaction(session.ledger, session.candidates)
        self._progress_queue = None

    def set_progress_queue(self, queue) -> None:
        self._progress_queue = queue

    async def _progress(self, stage: str, label: str) -> None:
        if self._progress_queue is not None:
            await self._progress_queue.put({"type": "stage", "stage": stage, "label": label})

    def _actor_ticket(self, npc_id: str) -> bool:
        npc = self.session.world.npcs.get(npc_id)
        if npc is None:
            return False
        return bool(npc.has_actor)

    async def _write_turn(
        self,
        player_input: str,
        *,
        scene: str,
        rule_bundle: dict | None = None,
        rewrite_note: str = "",
        writer_directive: str = "",
    ):
        """Single-agent write pass, with the deep-choice two-stage loop.

        First call: the writer emits the prose, or emits actor_questions for
        NPCs facing deep choices. If questions exist and the NPC holds an
        actor ticket, run the isolated Actor and re-invoke the writer once
        with the decisions; otherwise act as ghostwriter and keep the output.
        """
        await self._progress("writer", "编排成文中")
        out = await run_writer(
            self.llm,
            self.session.world,
            self.session.ledger,
            player_input,
            rule_bundle=rule_bundle,
            scene_id=scene,
            preset=self.preset,
            rewrite_note=rewrite_note,
            writer_directive=writer_directive,
            model=self.settings.resolved_model("story"),
            temperature=0.8,
        )

        # Deep choices are delegated to the NPCs' isolated Actors.
        questions = [q for q in out.actor_questions if self._actor_ticket(q.npc_id)]
        if questions:
            await self._progress("actor", "NPC Agent 思考中")
            decisions = []
            for q in questions:
                npc = self.session.world.npcs[q.npc_id]
                decision = await run_actor(
                    self.llm,
                    self.session.world,
                    self.session.ledger,
                    npc,
                    q.question,
                    context=q.context,
                    scene_id=scene,
                    model=self.settings.resolved_model("actor"),
                    temperature=0.8,
                )
                decisions.append(
                    f"- {npc.name}（{npc.id}）：{decision.decision}；行为：{decision.action_hint}；语气：{decision.tone}"
                )
            await self._progress("writer", "按NPC决策成文中")
            out = await run_writer(
                self.llm,
                self.session.world,
                self.session.ledger,
                player_input,
                rule_bundle=rule_bundle,
                scene_id=scene,
                preset=self.preset,
                actor_decisions="\n".join(decisions),
                rewrite_note=rewrite_note,
                writer_directive=writer_directive,
                model=self.settings.resolved_model("story"),
                temperature=0.8,
            )

        return out

    def _deferred_actor_notes(self, out) -> list[dict]:
        """Notes for deep choices not dispatched to an isolated Actor.

        Two cases: a second-pass question with a ticket (recursion guard, we
        only run one two-stage loop per turn), or a question for an NPC
        without a ticket (ghostwritten by the writer). Both are surfaced as
        minor issues instead of being silently dropped.
        """
        notes = []
        for q in out.actor_questions:
            npc = self.session.world.npcs.get(q.npc_id)
            name = npc.name if npc else q.npc_id
            if self._actor_ticket(q.npc_id):
                desc = f"二稿仍提出{name}的未决深抉择（{q.question[:40]}），本回合不再追派 Actor"
            else:
                desc = f"{name} 无 Actor 配给，该深抉择已由编剧代笔（{q.question[:40]}）"
            notes.append({"level": "minor", "desc": desc})
        return notes

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    async def run_turn(self, player_input: str) -> Candidate:
        pending = self.session.candidates.list_pending()
        if pending:
            if len(pending) == 1:
                await self.transaction.commit(pending[0].candidate_id, audit=self._audit_callback)
            else:
                raise NeedChooseCandidate(
                    "当前回合有多份候选，请先选择采纳哪一份或放弃当前回合。"
                )

        # 本回合导演指令：((...)) 行内语法，剥离后不进路由/不进账本。
        player_input, writer_directive = _extract_directive(player_input)

        route = classify_input(player_input, self.session.world)
        scene = self.session.ledger.save.player_scene
        rule_bundle: dict = {"route": route, "scene": scene}

        # Rule pre-solve for move: only when the destination actually
        # resolves. Unresolved moves leave scene/clock to the audit, which
        # settles location/registration from the adopted prose.
        if route == "move":
            dest = resolve_destination(player_input, self.session.world, self.session.ledger)
            if dest:
                delta = travel_minutes(scene, dest, self.session.world)
                rule_bundle.update({"destination": dest, "delta_minutes": delta, "scene": dest})
                scene = dest
        elif route == "jump":
            if "第二天" in player_input or "明天" in player_input:
                delta = 12 * 60
            elif "晚上" in player_input:
                delta = 4 * 60
            elif "中午" in player_input:
                delta = 2 * 60
            else:
                delta = 60
            rule_bundle["delta_minutes"] = delta

        # World book pre-solve: this turn's player input triggers concepts
        # (merge into the active list previous prose facts still hold slots).
        hits = hit_entry_ids(self.session.world, player_input)
        if hits:
            self.session.ledger.save.active_lore_ids = merge_active_lore(
                self.session.ledger.save.active_lore_ids, hits
            )

        out = await self._write_turn(
            player_input,
            scene=scene,
            rule_bundle=rule_bundle,
            writer_directive=writer_directive,
        )

        if self.settings.qc_enabled:
            await self._progress("qc", "质检员审校中")
            qc_participants = ["player"] + [q.npc_id for q in out.actor_questions]
            qc = await run_qc(
                self.llm,
                self.session.world,
                self.session.ledger,
                out.prose,
                participants=qc_participants,
                preset=self.preset,
                reference=build_qc_reference(self.session.world, self.session.ledger, qc_participants),
                summary_hint=out.summary,
                model=self.settings.resolved_model("qc"),
                temperature=self.settings.temp_qc,
            )
            prose = qc.prose
            issues = qc.issues
            # 摘要随正文一同过质检：正文被脱敏/改写而摘要未同步时，以质检修正版为准
            # （防泄漏经事件日志摘要侧门回归——summary 会长期反复装配）。
            summary_hint = qc.summary or out.summary
        else:
            # 跳过质检：编剧初稿直接进候选（设置项，玩家自担文风/泄漏风险）。
            prose = out.prose
            issues = []
            summary_hint = out.summary

        # Build one candidate version.
        summary = summary_hint or (prose or "")[:40]
        turn_id = new_id("turn")
        candidate_id = new_id("cand")
        now = dt.datetime.now().isoformat(timespec="seconds")
        candidate = Candidate(
            candidate_id=candidate_id,
            turn_id=turn_id,
            trace_id=new_id("tr"),
            mode="initial",
            player_input=player_input,
            writer_directive=writer_directive,
            prose=prose,
            side_effects=SideEffects(
                delta_minutes=rule_bundle.get("delta_minutes", 0),
                # 规则段可定移动目的地；正文语义副作用（时间/在场/私密）由采纳时审计推断。
                narrative={
                    "location": rule_bundle.get("destination"),
                    "summary": summary,
                },
                events=[],
            ),
            conflicts=issues + self._deferred_actor_notes(out),
            created_at=now,
            updated_at=now,
        )
        self.session.candidates.save(candidate)
        return candidate

    async def reroll(self, turn_id: str, mode: str = "rephrase", note: str = "") -> Candidate:
        turn_candidates = self.session.candidates.list_for_turn(turn_id)
        if not turn_candidates:
            raise KeyError(f"no candidates for turn: {turn_id}")
        latest = turn_candidates[-1]

        scene = (
            latest.side_effects.narrative.get("location")
            if latest.side_effects.narrative
            else self.session.ledger.save.player_scene
        )
        rewrite_note = ""
        if mode in {"rephrase", "retarget"}:
            parts = ["围绕上一稿，保持同一剧情走向重写正文"]
            if note:
                parts.append(f"（玩家要求：{note}）")
            rewrite_note = "；".join(parts)

        out = await self._write_turn(
            latest.player_input,
            scene=scene,
            rewrite_note=rewrite_note,
            writer_directive=latest.writer_directive,
        )

        if self.settings.qc_enabled:
            await self._progress("qc", "质检员审校中")
            qc = await run_qc(
                self.llm,
                self.session.world,
                self.session.ledger,
                out.prose,
                preset=self.preset,
                reference=build_qc_reference(self.session.world, self.session.ledger, []),
                summary_hint=out.summary,
                model=self.settings.resolved_model("qc"),
                temperature=self.settings.temp_qc,
            )
            prose = qc.prose
            issues = qc.issues
            summary_hint = qc.summary or out.summary
        else:
            prose = out.prose
            issues = []
            summary_hint = out.summary

        side_effects = latest.side_effects.model_copy(deep=True)
        if side_effects.narrative is not None:
            side_effects.narrative["summary"] = summary_hint or (prose or "")[:40]

        now = dt.datetime.now().isoformat(timespec="seconds")
        candidate = Candidate(
            candidate_id=new_id("cand"),
            turn_id=turn_id,
            trace_id=latest.trace_id,
            mode=mode,
            player_input=latest.player_input,
            writer_directive=latest.writer_directive,
            prose=prose,
            side_effects=side_effects,
            conflicts=issues + self._deferred_actor_notes(out),
            created_at=now,
            updated_at=now,
        )
        self.session.candidates.save(candidate)
        return candidate

    def discard(self, turn_id: str) -> None:
        self.transaction.discard_turn(turn_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _audit_callback(self, candidate):
        """Audit input adapter: run_audit now takes prose + player input."""
        return await run_audit(
            self.llm,
            self.session.world,
            self.session.ledger,
            candidate.prose,
            candidate.player_input or "",
            model=self.settings.resolved_model("audit"),
            temperature=0.2,
        )

    async def adopt(self, candidate_id: str) -> None:
        await self.transaction.commit(candidate_id, audit=self._audit_callback)