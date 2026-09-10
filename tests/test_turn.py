from __future__ import annotations

import asyncio

import pytest

from app.core.llm import FakeLLM
from app.rules.lorebook import merge_active_lore
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
        "location": "网吧",
        "participants": ["player", "朱明"],
        "private": False,
        "adopt_player_body": False,
        "actor_questions": [
            {
                "npc_id": "朱明",
                "question": "被追问打架的旧事，朱明是含糊带过还是翻脸？",
                "context": "玩家问朱明昨天为什么打架，朱明想起父亲欠债的由头",
            }
        ],
    }
    actor_out = {"decision": "含糊带过", "action_hint": "岔开话题", "tone": "心虚"}
    writer_second = {
        "prose": "朱明把脸别过去，含糊带过，岔开话题。",
        "summary": "朱明含糊带过打架的事。",
        "location": "网吧",
        "participants": ["player", "朱明"],
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
    assert "含糊带过" in second_input and "朱明" in second_input
    assert candidate.side_effects.narrative["summary"] == writer_second["summary"]


def test_actor_questions_without_ticket_stay_ghostwritten(session, settings):
    """actor_questions for an NPC without has_actor are not dispatched:
    the first-pass prose is kept and no Actor call happens."""
    writer_out = {
        "prose": "王蓉低头想了想。",
        "summary": "王蓉考虑刘星的请求。",
        "location": "主街",
        "participants": ["player", "王蓉"],
        "private": False,
        "adopt_player_body": False,
        "actor_questions": [
            {"npc_id": "王蓉", "question": "王蓉是否帮忙？", "context": "玩家请王蓉修电脑"}
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
        "participants": ["player", "王蓉"],
        "private": True,
        "delta_minutes": 240,
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
    assert ev["known_by"] == ["player", "王蓉"]  # private（名单确定性排序）
    assert ev["location_name"] == "苇草深处"
    # One-shot scene is NOT registered in the world instance.
    assert not any(s.id == "weicao_deep" for s in session.world.scenes)
    # Presence follows the audit inference.
    assert "朱明" not in session.ledger.present_at("weicao_deep")
    assert session.ledger.save.player_scene == "weicao_deep"


def test_audit_registers_reusable_scene(session, settings):
    """A scene the player declares reusable gets registered (M17 转正)。
    键=中文名后，审计直接给出中文 id，register_scene 按该键落表。"""
    audit_out = {
        "location": "街角奶茶店",
        "scene_name": "街角奶茶店",
        "register_scene": True,
        "participants": ["player"],
        "private": False,
        "delta_minutes": 0,
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

    assert any(s.id == "街角奶茶店" for s in session.world.scenes)
    assert session.ledger.save.player_scene == "街角奶茶店"


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


def test_lore_trigger_input_merges_then_adopt_rebuilds(session, settings):
    """World book v2: player input hits keywords at pre-solve (merged into
    the active list), and adopt clears + rebuilds from the adopted prose."""
    writer_out = {
        "prose": "你走进网吧，看见朱明正在跟老周说话。",
        "summary": "网吧遇见朱明。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "网吧",
        "scene_name": "网吧",
        "register_scene": False,
        "participants": ["player", "朱明"],
        "private": False,
        "delta_minutes": 0,
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

    # Pre-solve: player input hits the net_bar_fire entry ("网吧").
    candidate = asyncio.run(runner.run_turn("去网吧找朱明"))
    assert session.ledger.save.active_lore_ids == ["net_bar_fire"]

    # The writer's first call saw the triggered entry's body in the work order.
    # （2026-09-10 起世界书块不再输出条目 id 前缀，只注入正文全文。）
    writer_call = llm.calls[0]
    system = writer_call["messages"][0]["content"]
    assert "包夜十块钱" in system

    # Adopt: the active list is rebuilt from the adopted prose, which also
    # mentions 网吧 — the entry survives by content, not by the input.
    asyncio.run(runner.adopt(candidate.candidate_id))
    assert session.ledger.save.active_lore_ids == ["net_bar_fire"]

    # A turn whose input and prose mention nothing in the book clears it.
    llm2 = PrefixKeyLLM(
        {
            "玩家输入：": {
                "prose": "你坐在海边石头上发呆。",
                "summary": "海边发呆。",
                "actor_questions": [],
            },
            "正文：": {"status": "pass", "prose": "你坐在海边石头上发呆。", "issues": []},
            "已采纳正文": {
                "location": "主街",
                "scene_name": "主街",
                "register_scene": False,
                "participants": ["player"],
                "private": False,
                "delta_minutes": 0,
                    "lifecycle": [],
            },
        }
    )
    runner2 = _runner(session, llm2, settings)
    candidate2 = asyncio.run(runner2.run_turn("发呆"))
    # 输入未命中：上一轮的事实命中保留（列表 = 上轮回复 + 本轮输入）。
    assert session.ledger.save.active_lore_ids == ["net_bar_fire"]
    asyncio.run(runner2.adopt(candidate2.candidate_id))
    assert session.ledger.save.active_lore_ids == []


def test_lore_merge_dedup_and_cap(session, settings):
    """Input hits merge deduped with existing entries, capped at 5."""
    writer_out = {
        "prose": "你路过鱼市，打算买条鱼。",
        "summary": "路过鱼市。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "鱼市",
        "scene_name": "鱼市",
        "register_scene": False,
        "participants": ["player"],
        "private": False,
        "delta_minutes": 0,
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

    # Seed a prior prose hit on 网吧 → active = [net_bar_fire].
    session.ledger.save.active_lore_ids = ["net_bar_fire"]
    candidate = asyncio.run(runner.run_turn("去鱼市买条鱼"))
    active = session.ledger.save.active_lore_ids
    assert active[0] == "net_bar_fire"  # 上轮事实优先
    assert active == ["net_bar_fire", "fish_market"]

    # 已有条目不重复追加，且超出上限被截断。
    hits = ["fish_market", "zhang_grievance", "fishback", "net_bar_fire", "net_bar_fire", "fish_market"]
    session.ledger.save.active_lore_ids = []
    merged = merge_active_lore(session.ledger.save.active_lore_ids, hits)
    assert merged == ["fish_market", "zhang_grievance", "fishback", "net_bar_fire"]

    asyncio.run(runner.adopt(candidate.candidate_id))
    assert session.ledger.save.active_lore_ids == ["fish_market"]


def test_player_notes_reach_writer_work_order(session, settings):
    """主角与 NPC 字段一致：幕后注/自知隐秘进编剧工作单，不进 Actor 切片。"""
    from app.core.workorder import build_work_order

    session.ledger.save.player.private_note = "其实是镇长的私生子"
    session.ledger.save.player.personal_secrets = "欠了赌债"
    session.world.npcs["朱明"].private_note = "网吧冬天会失火"
    session.world.npcs["朱明"].personal_secrets = "他爸欠了赌债"
    # 让朱明的近况成立：seed 一条他在 网吧 的定位事件。
    session.ledger.append(
        {
            "id": session.ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T08:00:00",
            "location": "网吧",
            "participants": ["朱明"],
            "known_by": None,
            "body": "朱明在网吧。",
            "summary": "朱明在网吧。",
            "player_input": None,
            "source": "seed",
        }
    )

    writer = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "主角·你" in writer
    assert "其实是镇长的私生子" in writer
    assert "欠了赌债" in writer
    assert "网吧冬天会失火" in writer
    assert "背景：" not in writer

    actor = build_work_order("actor_朱明", session.world, session.ledger, "网吧")
    assert "其实是镇长的私生子" not in actor  # 主角底牌不泄漏进 NPC 切片
    assert "网吧冬天会失火" not in actor  # 朱明的幕后注（他也不知道）不给他自己看
    assert "他爸欠了赌债" in actor  # 朱明自知隐秘进他自己的切片

    # QC 参照区：玩家秘密是禁区（NPC 提及=泄漏；正文揭示=泄漏）。
    from app.workers.qc import build_qc_reference

    reference = build_qc_reference(session.world, session.ledger, ["player", "朱明"])
    assert "假面骑士" not in reference  # 无秘密时的基线
    session.ledger.save.player.personal_secrets = "夜里是假面骑士"
    session.ledger.save.player.private_note = "受神之加护，赐予者名雅典娜"
    reference = build_qc_reference(session.world, session.ledger, ["player", "朱明"])
    assert "假面骑士" in reference
    assert "雅典娜" in reference
    assert "只有玩家自己知道" in reference
    assert "连玩家也不知道" in reference

def test_audit_npc_moves_updates_snapshot(session, settings):
    """npc_moves 离场落账：正文明确写出某 NPC 离开去别处 → 轻量位置事件
    更新其快照（从当前场景名单消失）；去向是私密名单制（NPC 本人 + 玩家）。"""
    writer_out = {
        "prose": "刘星在网吧找到通宵的朱明，聊了几句，朱明先回家了。",
        "summary": "刘星在网吧找到朱明，聊几句后朱明回家。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "网吧",
        "participants": ["player", "朱明"],
        "private": False,
        "delta_minutes": 15,
        "npc_moves": [
            {"npc_id": "朱明", "location": "zhuming_home", "scene_name": "朱明家"},
            {"npc_id": "npc_ghost", "location": "somewhere"},  # 未知 npc → 静默跳过
        ],
        "lifecycle": [],
    }
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("去网吧看看，朱明是否在"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    evs = session.ledger.narratives
    main, move = evs[-2], evs[-1]
    # 主事件：朱明是被找到的互动对象 → 参与者
    assert "朱明" in main["participants"]
    # 离场事件：轻量位置账目，去向私密（本人 + 目击玩家）
    assert move["participants"] == ["朱明"]
    assert move["known_by"] == ["player", "朱明"]
    assert move["location"] == "zhuming_home"
    assert "朱明前往" in move["summary"]
    # 快照更新：朱明从网吧在场名单消失（快照已随离场事件走到未注册的"朱明家"）
    assert "朱明" not in session.ledger.present_at("网吧")
    # 他的已知集含亲历：主事件（被找到聊天）+ 自己的移动
    got = "\n".join(session.ledger.known_set("朱明", "网吧"))
    assert "朱明前往" in got
    # 非法条目静默跳过：未知 npc_id 不落账
    assert not any(e["location"] == "somewhere" for e in evs)

def test_audit_invented_id_rescued_by_alias(session, settings):
    """审计自造拼音 id（鱼市→yushi）的机械纠偏：location 不在场景表但
    scene_name 命中注册场景的中文名/别名 → 改写为注册 id（npc_moves 同）。"""
    writer_out = {
        "prose": "刘星和朱明去鱼市拿了货，朱明拎着货先回家了。",
        "summary": "刘星和朱明在鱼市拿货，朱明携货回家。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "yushi",  # 审计自造的拼音 id，场景表里没有
        "scene_name": "鱼市",
        "participants": ["player", "朱明"],
        "private": False,
        "delta_minutes": 40,
        "npc_moves": [
            {"npc_id": "朱明", "location": "zhuming_jia", "scene_name": "朱明家"}
        ],
        "lifecycle": [],
    }
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("去鱼市拿货"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    evs = session.ledger.narratives
    main, move = evs[-2], evs[-1]
    # 主事件 location 被别名救回注册 id，不再是幻影
    assert main["location"] == "鱼市"
    assert "location_name" not in main  # 注册场景不需要显示名兜底
    assert session.ledger.save.player_scene == "鱼市"
    # 离场事件：目的地未注册且 scene_name 也不命中 → 原样保留（一次性布景）
    assert move["location"] == "zhuming_jia"
    assert "朱明" not in session.ledger.present_at("鱼市")

def test_audit_clock_to_overrides_delta(session, settings):
    """正文明确说了故事时间走到几点 → 审计 clock_to 绝对对钟，
    delta_minutes 被涵盖不再叠加；格式坏则退回估时长。"""
    writer_out = {
        "prose": "不知不觉到了晚上八点，刘星和王蓉在网吧聊完了正事。",
        "summary": "晚上八点刘星和王蓉在网吧聊完正事。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "网吧",
        "participants": ["player", "王蓉"],
        "private": False,
        "delta_minutes": 999,  # 若未对钟会被保险丝截到 960——对钟后必须被忽略
        "clock_to": "2026-07-14T20:00:00",
        "lifecycle": [],
    }
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("和王蓉在网吧把事情谈完"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    # 08:00 → 直接对钟 20:00，而非 08:00+960min(=隔天 00:00)
    assert session.ledger.save.clock == "2026-07-14T20:00:00"
    assert session.ledger.narratives[-1]["at"] == "2026-07-14T20:00:00"

def test_lore_always_on_always_injected(session):
    """常驻条目（always_on）：不需要关键词触发，每轮必注入世界书块；
    不占触发名额（5 条上限只管关键词命中）。"""
    from app.core.workorder import build_work_order
    from app.world.models import LoreEntry

    session.ledger.world.lorebook.append(
        LoreEntry(id="lore_tide", keywords=[], body="镇外的潮汐每月十五最盛。", always_on=True)
    )
    # 零触发状态（active_lore_ids 空）：常驻条目仍然出现
    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "【常驻】镇外的潮汐每月十五最盛。" in order

    # 塞满 5 条触发名额：常驻条目不受影响，第 6 条触发被上限挡住
    for i in range(6):
        session.ledger.world.lorebook.append(
            LoreEntry(id=f"lore_hit{i}", keywords=[f"线索{i}"], body=f"背景{i}")
        )
    from app.rules.lorebook import merge_active_lore
    session.ledger.save.active_lore_ids = merge_active_lore(
        [], [f"lore_hit{i}" for i in range(6)]
    )
    assert len(session.ledger.save.active_lore_ids) == 5
    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "【常驻】镇外的潮汐每月十五最盛。" in order
    assert "背景4" in order      # 触发第 5 条（上限内）
    assert "背景5" not in order  # 第 6 条被上限挡住
