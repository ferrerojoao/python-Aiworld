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
from app.workers.director import run_director
from app.workers.qc import run_qc
from app.workers.schemas import Beat
from app.workers.storyteller import run_storyteller


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

    def _storyteller_context(self, directive, fallback_scene: str) -> dict:
        scene_id = directive.location or fallback_scene
        scene_obj = next((s for s in self.session.world.scenes if s.id == scene_id), None)
        present_ids = self.session.ledger.present_at(scene_id)
        present_npcs = []
        for pid in present_ids:
            npc = self.session.world.npcs.get(pid)
            if npc:
                present_npcs.append(
                    {"name": npc.name, "appearance": npc.appearance, "persona": npc.persona}
                )
        lore_bodies = []
        for ref in directive.lore_refs:
            entry = next((e for e in self.session.world.lorebook if e.id == ref), None)
            if entry:
                lore_bodies.append(entry.body)
        return {
            "present_npcs": present_npcs,
            "scene_name": scene_obj.name if scene_obj else "",
            "scene_desc": scene_obj.perceivable if scene_obj else "",
            "lore_bodies": lore_bodies,
        }

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

        await self._progress("director", "导演安排中")
        directive = await run_director(
            self.llm,
            self.session.world,
            self.session.ledger,
            player_input,
            rule_bundle=rule_bundle,
            scene_id=scene,
            preset=self.preset,
            model=self.settings.resolved_model("director"),
            temperature=0.7,
        )

        # If the director returned no beats, give the storyteller at least
        # the player's input so it does not improvise meta/system text.
        if not directive.beats and not directive.adopt_player_body:
            directive.beats.append(Beat(kind="narrate", text=f"玩家想：{player_input}"))

        # Resolve any actor nodes generated by the director.
        if any(b.kind == "actor" for b in directive.beats):
            await self._progress("actor", "NPC Agent 思考中")
        for beat in directive.beats:
            if beat.kind == "actor" and beat.actor_npc_id:
                npc = self.session.world.npcs.get(beat.actor_npc_id)
                if npc is not None:
                    memories = "\n".join(
                        self.session.ledger.experiences(npc.id, npc.id)[-5:]
                    )
                    context = f"{beat.meaning or ''} {beat.tone_hint or ''}".strip()
                    if memories:
                        context += f"\n你记得的事：\n{memories}"
                    decision = await run_actor(
                        self.llm,
                        npc,
                        beat.text or beat.meaning or "深抉择",
                        context=context,
                        model=self.settings.resolved_model("actor"),
                        temperature=0.8,
                    )
                    beat.resolved = decision.model_dump()

        await self._progress("storyteller", "说书人写作中")
        if directive.adopt_player_body and directive.beats:
            story = type("Story", (), {"prose": directive.beats[0].text, "time_hint": None})()
        else:
            story = await run_storyteller(
                self.llm,
                self.session.world,
                directive,
                preset=self.preset,
                player=self.session.ledger.save.player.model_dump(),
                **self._storyteller_context(directive, scene),
                model=self.settings.resolved_model("story"),
                temperature=0.9,
            )

        await self._progress("qc", "质检员审校中")
        qc = await run_qc(
            self.llm,
            self.session.world,
            self.session.ledger,
            story.prose,
            participants=directive.participants,
            preset=self.preset,
            model=self.settings.resolved_model("qc"),
            temperature=self.settings.temp_qc,
        )

        # Build one candidate version.
        turn_id = new_id("turn")
        candidate_id = new_id("cand")
        now = dt.datetime.now().isoformat(timespec="seconds")
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
                    "location": directive.location or scene,
                    "participants": directive.participants or ["player"],
                    "known_by": None if not directive.private else directive.participants,
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
        if mode == "redirect":
            await self._progress("director", "导演重排中")
            directive = await run_director(
                self.llm,
                self.session.world,
                self.session.ledger,
                latest.player_input,
                rule_bundle={"mode": mode, "note": note, "scene": scene},
                scene_id=scene,
                preset=self.preset,
                model=self.settings.resolved_model("director"),
                temperature=0.7,
            )
            await self._progress("storyteller", "说书人写作中")
            story = await run_storyteller(
                self.llm,
                self.session.world,
                directive,
                preset=self.preset,
                player=self.session.ledger.save.player.model_dump(),
                **self._storyteller_context(directive, scene),
                model=self.settings.resolved_model("story"),
                temperature=0.9,
            )
        else:
            # rephrase / retarget reuse the existing directive's visible shape
            # by re-running storyteller with an extra instruction.
            from app.workers.schemas import Directive

            extra = f"（玩家要求：{note}）" if note else ""
            beats = [{"kind": "narrate", "text": extra}] if extra else [
                {"kind": "narrate", "text": f"围绕上一稿重写：{latest.prose[:100]}"}
            ]
            dummy = Directive(
                mode="scene",
                beats=beats,
                location=latest.side_effects.narrative.get("location") if latest.side_effects.narrative else scene,
                participants=latest.side_effects.narrative.get("participants") if latest.side_effects.narrative else ["player"],
            )
            await self._progress("storyteller", "说书人写作中")
            story = await run_storyteller(
                self.llm,
                self.session.world,
                dummy,
                player=self.session.ledger.save.player.model_dump(),
                **self._storyteller_context(dummy, scene),
                model=self.settings.resolved_model("story"),
                temperature=0.9,
            )

        await self._progress("qc", "质检员审校中")
        qc = await run_qc(
            self.llm,
            self.session.world,
            self.session.ledger,
            story.prose,
            preset=self.preset,
            model=self.settings.resolved_model("qc"),
            temperature=self.settings.temp_qc,
        )

        now = dt.datetime.now().isoformat(timespec="seconds")
        candidate = Candidate(
            candidate_id=new_id("cand"),
            turn_id=turn_id,
            trace_id=latest.trace_id,
            mode=mode,
            player_input=latest.player_input,
            prose=qc.prose,
            side_effects=latest.side_effects,
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