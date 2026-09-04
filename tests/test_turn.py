from __future__ import annotations

import asyncio

import pytest

from app.runtime.turn import NeedChooseCandidate, TurnRunner


def _runner(session, fake_llm, settings):
    return TurnRunner(session, fake_llm, settings)


def test_jump_advances_delta(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("等到晚上"))
    assert candidate.side_effects.delta_minutes == 240


def test_query_bypass_creates_read_only_candidate(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("现在什么时辰"))
    assert candidate.mode == "query"
    assert candidate.side_effects.narrative is None


def test_run_turn_creates_pending_candidate_without_writing_ledger(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("去网吧找朱明，问他昨天为什么打架"))

    assert candidate.prose
    assert len(session.candidates.list_pending()) == 1
    assert len(session.ledger.events) == 0


def test_reroll_keeps_multiple_candidates(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    first = asyncio.run(runner.run_turn("问朱明昨天的事"))
    second = asyncio.run(runner.reroll(first.turn_id, mode="rephrase", note="写得温和一点"))

    assert first.candidate_id != second.candidate_id
    assert second.turn_id == first.turn_id
    assert len(session.candidates.list_for_turn(first.turn_id)) == 2


def test_adopt_commits_one_and_cleans_other_candidates(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    first = asyncio.run(runner.run_turn("问朱明昨天的事"))
    second = asyncio.run(runner.reroll(first.turn_id, mode="rephrase"))

    asyncio.run(runner.adopt(second.candidate_id))

    assert len(session.ledger.narratives) == 1
    assert session.ledger.narratives[0]["body"] == second.prose
    assert session.candidates.list_for_turn(first.turn_id) == []


def test_next_input_auto_adopts_single_candidate(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    first = asyncio.run(runner.run_turn("问朱明昨天的事"))

    # The next turn sees exactly one pending candidate and auto-adopts it.
    second = asyncio.run(runner.run_turn("然后怎么办"))

    assert len(session.ledger.narratives) == 1
    assert session.ledger.narratives[0]["body"] == first.prose
    assert len(session.candidates.list_pending()) == 1  # only the new candidate


def test_multiple_candidates_blocks_new_turn(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    first = asyncio.run(runner.run_turn("问朱明昨天的事"))
    asyncio.run(runner.reroll(first.turn_id, mode="redirect"))

    with pytest.raises(NeedChooseCandidate):
        asyncio.run(runner.run_turn("继续问"))


def test_discard_removes_all_candidates(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    first = asyncio.run(runner.run_turn("问朱明昨天的事"))
    asyncio.run(runner.reroll(first.turn_id, mode="rephrase"))

    runner.discard(first.turn_id)
    assert session.candidates.list_for_turn(first.turn_id) == []