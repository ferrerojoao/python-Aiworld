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
        candidate = self.candidates.load(candidate_id)
        if candidate is None:
            raise KeyError(f"candidate not found: {candidate_id}")

        turn_id = candidate.turn_id
        clock = self.ledger.save.clock or "2026-07-14T08:00:00"
        # Clock is a simple ISO string; MVP advances by minutes.
        if candidate.side_effects.delta_minutes:
            try:
                parsed = dt.datetime.fromisoformat(clock)
                parsed += dt.timedelta(minutes=candidate.side_effects.delta_minutes)
                clock = parsed.replace(microsecond=0).isoformat()
            except ValueError:
                pass
        self.ledger.save.clock = clock

        if candidate.side_effects.narrative is not None:
            event_id = self.ledger.allocate_event_id()
            narrative = {
                "id": event_id,
                "kind": "narrative",
                "at": clock,
                "location": candidate.side_effects.narrative.get("location") or self.ledger.save.player_scene,
                "participants": candidate.side_effects.narrative.get("participants") or ["player"],
                "known_by": candidate.side_effects.narrative.get("known_by"),
                "body": candidate.prose,
                "summary": candidate.side_effects.narrative.get("summary"),
                "player_input": candidate.player_input or None,
                "source": "turn",
            }
            self.ledger.append(narrative)
            if candidate.side_effects.narrative.get("location"):
                self.ledger.save.player_scene = candidate.side_effects.narrative["location"]

        for event in candidate.side_effects.events:
            event = dict(event)
            event.setdefault("id", self.ledger.allocate_event_id())
            event.setdefault("kind", "narrative")
            event.setdefault("at", clock)
            self.ledger.append(event)

        # v1 relation axes are reserved; just copy values if provided.
        for key, value in candidate.side_effects.axes.items():
            self.ledger.save.axes[key] = value

        # Audit runs synchronously after the narrative is recorded.
        if audit is not None:
            last_narrative = next(
                (ev for ev in reversed(self.ledger.events) if ev["kind"] == "narrative"),
                None,
            )
            if last_narrative is not None:
                try:
                    await audit(last_narrative)
                except Exception as exc:  # noqa: BLE001
                    # Audit failure must not roll back the already-adopted
                    # narrative, but it must not be silent either.
                    self.ledger.save.audit_last_error = f"{type(exc).__name__}: {exc}"

        self.ledger.persist_save()
        # Clean this turn's candidate files only after a successful commit.
        self.candidates.delete_turn(turn_id)

    def discard_turn(self, turn_id: str) -> None:
        self.candidates.delete_turn(turn_id)