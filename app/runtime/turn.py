from __future__ import annotations

import datetime as dt

from app.config import Settings
from app.core.store import new_id
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
        entity = self.session.ledger.save.entities.get(npc_id)
        forced = bool(entity and entity.forced_actor)
        return bool(npc.has_actor or forced)

    async def _write_turn(
        self,
        player_input: str,
        *,
        scene: str,
        rule_bundle: dict | None = None,
        rewrite_note: str = "",
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
                model=self.settings.resolved_model("story"),
                temperature=0.8,
            )

        if out.adopt_player_body:
            out.prose = player_input
        return out

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

        route = classify_input(player_input, self.session.world)
        scene = self.session.ledger.save.player_scene
        rule_bundle: dict = {"route": route, "scene": scene}

        # Rule pre-solve for move.
        if route == "move":
            dest = resolve_destination(player_input, self.session.world, self.session.ledger)
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

        if route == "query":
            from app.rules.scenes import scene_description

            clock = self.session.ledger.save.clock or "-"
            desc = scene_description(scene, self.session.world, self.session.ledger)
            now = dt.datetime.now().isoformat(timespec="seconds")
            candidate = Candidate(
                candidate_id=new_id("cand"),
                turn_id=new_id("turn"),
                trace_id=new_id("tr"),
                mode="query",
                player_input=player_input,
                prose=f"现在是 {clock}。{desc}",
                side_effects=SideEffects(),
                conflicts=[],
                created_at=now,
                updated_at=now,
            )
            self.session.candidates.save(candidate)
            return candidate

        out = await self._write_turn(
            player_input, scene=scene, rule_bundle=rule_bundle
        )

        await self._progress("qc", "质检员审校中")
        qc = await run_qc(
            self.llm,
            self.session.world,
            self.session.ledger,
            out.prose,
            participants=out.participants,
            preset=self.preset,
            reference=build_qc_reference(self.session.world, self.session.ledger, out.participants or []),
            model=self.settings.resolved_model("qc"),
            temperature=self.settings.temp_qc,
        )

        # Build one candidate version.
        summary = out.summary or (out.prose or "")[:40]
        turn_id = new_id("turn")
        candidate_id = new_id("cand")
        now = dt.datetime.now().isoformat(timespec="seconds")
        participants = out.participants or (["player"] + [q.npc_id for q in out.actor_questions])
        candidate = Candidate(
            candidate_id=candidate_id,
            turn_id=turn_id,
            trace_id=new_id("tr"),
            mode="initial",
            player_input=player_input,
            prose=qc.prose,
            side_effects=SideEffects(
                delta_minutes=rule_bundle.get("delta_minutes", 0),
                narrative={
                    "location": out.location or scene,
                    "participants": participants or ["player"],
                    "known_by": None if not out.private else (participants or ["player"]),
                    "summary": summary,
                },
                events=[],
            ),
            conflicts=qc.issues,
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
        )

        await self._progress("qc", "质检员审校中")
        qc = await run_qc(
            self.llm,
            self.session.world,
            self.session.ledger,
            out.prose,
            preset=self.preset,
            reference=build_qc_reference(self.session.world, self.session.ledger, out.participants or []),
            model=self.settings.resolved_model("qc"),
            temperature=self.settings.temp_qc,
        )

        side_effects = latest.side_effects.model_copy(deep=True)
        if side_effects.narrative is not None:
            side_effects.narrative["summary"] = out.summary or (out.prose or "")[:40]
            side_effects.narrative["location"] = out.location or scene
            if out.participants:
                side_effects.narrative["participants"] = out.participants

        now = dt.datetime.now().isoformat(timespec="seconds")
        candidate = Candidate(
            candidate_id=new_id("cand"),
            turn_id=turn_id,
            trace_id=latest.trace_id,
            mode=mode,
            player_input=latest.player_input,
            prose=qc.prose,
            side_effects=side_effects,
            conflicts=qc.issues,
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
    async def _audit_callback(self, narrative_event: dict):
        await run_audit(
            self.llm,
            self.session.ledger,
            narrative_event,
            model=self.settings.resolved_model("audit"),
            temperature=0.2,
        )

    async def adopt(self, candidate_id: str) -> None:
        await self.transaction.commit(candidate_id, audit=self._audit_callback)