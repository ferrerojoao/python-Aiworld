from __future__ import annotations

from app.runtime.transaction import Candidate, CandidateStore, SideEffects


def _cand(candidate_id: str, turn_id: str, prose: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        turn_id=turn_id,
        trace_id="tr",
        mode="initial",
        prose=prose,
        side_effects=SideEffects(),
    )


def test_reroll_keeps_old_candidates(tmp_path):
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_1", "turn_1", "版本一"))
    store.save(_cand("cand_2", "turn_1", "版本二"))
    store.save(_cand("cand_3", "turn_1", "版本三"))

    assert len(store.list_for_turn("turn_1")) == 3
    assert [c.prose for c in store.list_for_turn("turn_1")] == ["版本一", "版本二", "版本三"]


def test_adopt_cleanup_deletes_all_turn_candidates(tmp_path):
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_1", "turn_1", "版本一"))
    store.save(_cand("cand_2", "turn_1", "版本二"))
    store.save(_cand("cand_3", "turn_2", "另一回合"))

    store.delete_turn("turn_1")
    assert store.list_for_turn("turn_1") == []
    assert len(store.list_pending()) == 1


def test_discard_removes_whole_turn(tmp_path):
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_1", "turn_1", "版本一"))
    store.save(_cand("cand_2", "turn_1", "版本二"))
    store.delete_turn("turn_1")
    assert store.list_pending() == []