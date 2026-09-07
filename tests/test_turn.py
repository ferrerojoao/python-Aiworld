from __future__ import annotations

import asyncio

import pytest

from app.core.llm import FakeLLM
from app.runtime.turn import NeedChooseCandidate, TurnRunner


class PrefixKeyLLM(FakeLLM):
    """Match responses by the first user-message prefix, not exact key."""

    def _key(self, messages):
        for msg in reversed(messages):
            if msg.get("role") in {"user", "system"}:
                content = msg.get("content", "")
                for prefix in self.responses:
                    if content.startswith(prefix):
                        return prefix
        return "*"


def _runner(session, fake_llm, settings):
    return TurnRunner(session, fake_llm, settings)


def test_actor_two_stage_deep_choice(session, settings):
    """Writer raises actor_questions -> isolated Actor decides -> writer
    second pass writes by the decision; calls are writer, actor, writer, qc."""
    writer_first = {
        "prose": "朱明听完问题没接话。",
        "summary": "刘星追问打架的事。",
        "location": "net_bar",
        "participants": ["player", "npc_zhuming"],
        "private": False,
        "adopt_player_body": False,
        "actor_questions": [
            {
                "npc_id": "npc_zhuming",
                "question": "被追问打架的旧事，朱明是含糊带过还是翻脸？",
                "context": "玩家问朱明昨天为什么打架，朱明想起父亲欠债的由头",
            }
        ],
    }
    actor_out = {"decision": "含糊带过", "action_hint": "岔开话题", "tone": "心虚"}
    writer_second = {
        "prose": "朱明把脸别过去，含糊带过，岔开话题。",
        "summary": "朱明含糊带过打架的事。",
        "location": "net_bar",
        "participants": ["player", "npc_zhuming"],
        "private": False,
        "adopt_player_body": False,
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": "朱明把脸别过去，含糊带过，岔开话题。", "issues": []}
    llm = PrefixKeyLLM(
        {
            "玩家输入：": writer_first,
            "深抉择问题": actor_out,
            "以下深抉择已由": writer_second,
            "正文：": qc_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("问朱明昨天为什么打架"))

    assert candidate.prose == writer_second["prose"]
    assert len(llm.calls) == 4

    def has(call, prefix):
        return any((m.get("content") or "").startswith(prefix) for m in call["messages"])

    call0, call1, call2, call3 = llm.calls
    assert has(call0, "玩家输入：") and not has(call0, "深抉择问题")
    assert has(call1, "深抉择问题")
    assert has(call2, "以下深抉择已由")
    assert has(call3, "正文：")
    # the second pass received the actor's decision text
    second_input = " ".join(m.get("content", "") for m in call2["messages"])
    assert "含糊带过" in second_input and "npc_zhuming" in second_input
    assert candidate.side_effects.narrative["summary"] == writer_second["summary"]


def test_actor_questions_without_ticket_stay_ghostwritten(session, settings):
    """actor_questions for an NPC without has_actor are not dispatched:
    the first-pass prose is kept and no Actor call happens."""
    writer_out = {
        "prose": "王蓉低头想了想。",
        "summary": "王蓉考虑刘星的请求。",
        "location": "main_street",
        "participants": ["player", "npc_wangrong"],
        "private": False,
        "adopt_player_body": False,
        "actor_questions": [
            {"npc_id": "npc_wangrong", "question": "王蓉是否帮忙？", "context": "玩家请王蓉修电脑"}
        ],
    }
    qc_out = {"status": "pass", "prose": "王蓉低头想了想。", "issues": []}
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("请王蓉修电脑"))

    assert candidate.prose == "王蓉低头想了想。"
    # only writer + qc called, no actor pass
    assert len(llm.calls) == 2
    # the deferred question is surfaced instead of silently dropped
    assert any("王蓉" in (i.get("desc") or "") for i in candidate.conflicts)


def test_jump_advances_delta(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("等到晚上"))
    assert candidate.side_effects.delta_minutes == 240


def test_candidate_has_summary(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("去网吧找朱明"))
    assert candidate.side_effects.narrative.get("summary")


def test_run_turn_creates_pending_candidate_without_writing_ledger(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("去网吧找朱明，问他昨天为什么打架"))

    assert candidate.prose
    assert len(session.candidates.list_pending()) == 1
    # Only the opening event exists; the turn itself writes nothing until adopt.
    assert len(session.ledger.events) == 1
    assert session.ledger.events[0]["source"] == "opening"


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

    assert len(session.ledger.narratives) == 2  # opening + adopted turn
    assert session.ledger.narratives[-1]["body"] == second.prose
    assert session.ledger.narratives[-1]["player_input"] is not None
    assert session.candidates.list_for_turn(first.turn_id) == []


def test_next_input_auto_adopts_single_candidate(session, fake_llm, settings):
    runner = _runner(session, fake_llm, settings)
    first = asyncio.run(runner.run_turn("问朱明昨天的事"))

    # The next turn sees exactly one pending candidate and auto-adopts it.
    second = asyncio.run(runner.run_turn("然后怎么办"))

    assert len(session.ledger.narratives) == 2  # opening + adopted turn
    assert session.ledger.narratives[-1]["body"] == first.prose
    assert len(session.candidates.list_pending()) == 1  # only the new candidate


def test_audit_settles_side_effects_and_one_shot_scene(session, settings):
    """At adopt, the audit inference drives clock/location/presence/privacy
    and leaves one-shot scenes unregistered (display name only)."""
    writer_out = {
        "prose": "两人走到镇外一片苇草丛里，天渐渐黑了。王蓉悄悄说了一件心事。",
        "summary": "刘星和王蓉在苇草深处说话，天黑了。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": "两人走到镇外一片苇草丛里，天渐渐黑了。王蓉悄悄说了一件心事。", "issues": []}
    audit_out = {
        "location": "weicao_deep",
        "scene_name": "苇草深处",
        "register_scene": False,
        "participants": ["player", "npc_wangrong"],
        "private": True,
        "delta_minutes": 240,
        "hook_texts": [],
        "closed_hook_ids": [],
        "lifecycle": [],
    }
    llm = PrefixKeyLLM(
        {
            "玩家输入：": writer_out,
            "正文：": qc_out,
            "已采纳正文": audit_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("跟她出去走走"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert session.ledger.save.clock == "2026-07-14T12:00:00"  # 08:00 + 240min
    ev = session.ledger.narratives[-1]
    assert ev["location"] == "weicao_deep"
    assert ev["known_by"] == ["player", "npc_wangrong"]  # private
    assert ev["location_name"] == "苇草深处"
    # One-shot scene is NOT registered in the world instance.
    assert not any(s.id == "weicao_deep" for s in session.world.scenes)
    # Presence follows the audit inference.
    assert "npc_zhuming" not in session.ledger.present_at("weicao_deep")


def test_audit_registers_reusable_scene(session, settings):
    """A scene the player declares reusable gets registered (M17 转正)."""
    audit_out = {
        "location": "milktea_shop",
        "scene_name": "街角奶茶店",
        "register_scene": True,
        "participants": ["player"],
        "private": False,
        "delta_minutes": 0,
        "hook_texts": [],
        "closed_hook_ids": [],
        "lifecycle": [],
    }
    llm = PrefixKeyLLM(
        {
            "玩家输入：": {
                "prose": "你走进街角新开的奶茶店，点了杯柠檬水。",
                "summary": "刘星去了新开的奶茶店。",
                "actor_questions": [],
            },
            "正文：": {"status": "pass", "prose": "你走进街角新开的奶茶店，点了杯柠檬水。", "issues": []},
            "已采纳正文": audit_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("去街角那家奶茶店"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert any(s.id == "milktea_shop" and s.name == "街角奶茶店" for s in session.world.scenes)
    assert session.ledger.save.player_scene == "milktea_shop"


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