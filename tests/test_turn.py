from __future__ import annotations

import asyncio
import json

import pytest

from app.core.llm import FakeLLM
from app.core.workorder import STATE_TEXT_MAX
from app.ledger.save import EntityRuntime, StateItem
from app.rules.lorebook import merge_active_lore
from app.runtime.session import open_session
from app.runtime.turn import NeedChooseCandidate, TurnRunner
from app.world.models import SCENE_PLACEHOLDER


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


def _place(session, npc_id: str, scene: str) -> None:
    """把 NPC 安置到某场景（在场推导读账本里该角色的最后一条定位事件）。"""
    ledger = session.ledger
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T08:30:00",
            "location": scene,
            "participants": ["刘星", npc_id],
            "known_by": None,
            "body": f"{npc_id}在{scene}。",
            "summary": f"{npc_id}在{scene}。",
            "player_input": None,
            "source": "test",
        }
    )


def test_actor_two_stage_deep_choice(session, settings):
    """Writer raises actor_questions -> isolated Actor decides -> writer
    second pass rewrites by the decision, anchored on the first draft
    （一稿以 assistant 消息回填，二稿输出整场全文）;
    calls are writer, actor, writer, qc."""
    session.ledger.save.player_scene = "网吧"
    _place(session, "朱明", "网吧")

    writer_first = {
        "prose": "朱明听完问题没接话。",
        "summary": "刘星追问打架的事。",
        "location": "网吧",
        "participants": ["刘星", "朱明"],
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
        "participants": ["刘星", "朱明"],
        "private": False,
        "adopt_player_body": False,
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": "朱明把脸别过去，含糊带过，岔开话题。", "issues": []}
    llm = PrefixKeyLLM(
        {
            "玩家输入：": writer_first,
            "深抉择问题": actor_out,
            "【必须按本人决定重写的部分】": writer_second,
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
    assert has(call2, "【必须按本人决定重写的部分】")
    assert any("输出整场正文全文" in (m.get("content") or "") for m in call2["messages"])
    assert has(call3, "正文：")
    # 一稿以 assistant 消息回填进二稿（questions 已清空），不再被整体丢弃
    echoes = [
        m
        for m in call2["messages"]
        if m.get("role") == "assistant" and "朱明听完问题没接话" in (m.get("content") or "")
    ]
    assert echoes and '"actor_questions":[]' in echoes[0]["content"]
    # the second pass received the actor's decision text
    second_input = " ".join(m.get("content", "") for m in call2["messages"])
    assert "含糊带过" in second_input and "朱明" in second_input
    assert candidate.side_effects.narrative["summary"] == writer_second["summary"]


def test_actor_questions_without_ticket_force_a_second_pass(session, settings):
    """无票角色的上缴 = 编剧停笔，一稿残缺 → 不派 Actor，但**必须走第二稿**，
    明说「必须由你直接拍板」（2026-09-11：照用一稿必然缺戏；2026-09-12 去否定化，
    删掉原来的"不要把这一拍留空"尾巴）。"""
    writer_first = {
        "prose": "刘星和王蓉一起吃着早饭。",
        "summary": "刘星和王蓉吃早饭。",
        "actor_questions": [
            {"npc_id": "王蓉", "question": "王蓉是否帮忙？", "context": "玩家请王蓉修电脑"}
        ],
    }
    writer_second = {
        "prose": "刘星问王蓉能不能帮忙，王蓉低头想了想，答应了。",
        "summary": "刘星请王蓉帮忙，王蓉答应。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_second["prose"], "issues": []}
    llm = PrefixKeyLLM(
        {
            "玩家输入：": writer_first,
            "【本轮不派 Actor、必须由你直接拍板的部分": writer_second,
            "正文：": qc_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("请王蓉修电脑"))

    assert candidate.prose == writer_second["prose"]
    # writer + writer(补写) + qc：没有 Actor 调用，但一稿不再被照用
    assert len(llm.calls) == 3
    second_call = llm.calls[1]
    blob = " ".join((m.get("content") or "") for m in second_call["messages"])
    assert "王蓉" in blob and "必须由你直接拍板" in blob
    assert "不要把这一拍留空" not in blob
    assert any(m.get("role") == "assistant" for m in second_call["messages"])
    # 落点可见：无票的深抉择由编剧拍板（升格提示，不静默）
    assert any("王蓉" in (i.get("desc") or "") for i in candidate.conflicts)


def test_absent_ticketed_npc_is_not_dispatched(session, settings):
    """有票但不在场 → 不派 Actor（工作单按当前场景拼，缺席者拿到错位处境）；
    走第二稿由编剧拍板，稿子不悬空。"""
    writer_first = {
        "prose": "刘星在电话里跟朱明说起昨晚的事。",
        "summary": "刘星电话里问朱明。",
        "actor_questions": [
            {"npc_id": "朱明", "question": "要不要说出打架的原因？", "context": "电话那头的朱明"}
        ],
    }
    writer_second = {
        "prose": "电话那头朱明沉默了一会儿，含糊带过。",
        "summary": "朱明电话里含糊带过。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_second["prose"], "issues": []}
    llm = PrefixKeyLLM(
        {
            "玩家输入：": writer_first,
            "【本轮不派 Actor、必须由你直接拍板的部分": writer_second,
            "正文：": qc_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("给朱明打电话"))

    # 朱明有票但从未被记到任何场景 → 不派，走补写
    assert len(llm.calls) == 3
    assert any("不在当前场景" in (i.get("desc") or "") for i in candidate.conflicts)
    assert candidate.prose == writer_second["prose"]


def test_unresolved_npc_id_is_surfaced_not_silent(session, settings):
    """npc_id 填了「你」且文本里认不出是谁 → 不派但不静默：warning 带原始串，
    第二稿仍由编剧拍板补全（2026-09-11：填错 id 能过校验、却永远匹配不上）。"""
    writer_first = {
        "prose": "房间里只剩两个人对视。",
        "summary": "两人在房间里对视。",
        "actor_questions": [
            {"npc_id": "你", "question": "你怎么办？", "context": "对方盯着你"}
        ],
    }
    writer_second = {
        "prose": "对视三秒，你先开了口。",
        "summary": "你先开口打破沉默。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_second["prose"], "issues": []}
    llm = PrefixKeyLLM(
        {
            "玩家输入：": writer_first,
            "【本轮不派 Actor、必须由你直接拍板的部分": writer_second,
            "正文：": qc_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("和他对峙"))

    assert len(llm.calls) == 3
    hits = [i for i in candidate.conflicts if "无法识别" in (i.get("desc") or "")]
    assert hits and "你" in hits[0]["desc"]
    assert candidate.prose == writer_second["prose"]


def test_rule_bundle_solves_move_only(session, fake_llm, settings):
    """规则段只剩**移动**：目的地照旧预结算，时间不再预结算（2026-09-16 去规则化）。

    以前"第二天去鱼市"还会带一个 settle_to 绝对时刻进候选；现在这里必须没有
    ——时间由审计在采纳时独家结算（玩家给了具体时刻就照办、没给就自行估计）。
    """
    runner = _runner(session, fake_llm, settings)
    candidate = asyncio.run(runner.run_turn("第二天去鱼市"))
    assert candidate.side_effects.narrative["location"] == "鱼市"
    dumped = candidate.side_effects.model_dump()
    assert "settle_to" not in dumped
    assert "delta_minutes" not in dumped


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
        "participants": ["刘星", "王蓉"],
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
    assert ev["known_by"] == ["刘星", "王蓉"]  # private（名单确定性排序）
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
        "participants": ["刘星"],
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


def test_registered_scene_keeps_the_audit_alias(session, settings, world_root):
    """注册新场景时，审计同轮给的变体名要一并进 aliases（2026-09-16）。

    ``register_scene`` 原先只拿 ``location``，``scene_name`` 直接被丢掉——
    "老巷旧楼"落表后，"巷子深处的旧楼"这个审计知道的叫法就没人记得，
    以后要么认不出、要么被当成新地点重复注册。
    """
    audit_out = {
        "location": "村东苇塘",
        "scene_name": "苇塘",
        "register_scene": True,
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 0,
        "lifecycle": [],
    }
    llm = PrefixKeyLLM(
        {
            "玩家输入：": {
                "prose": "你绕到村东的苇塘边坐下。",
                "summary": "刘星去了村东苇塘。",
                "actor_questions": [],
            },
            "正文：": {"status": "pass", "prose": "你绕到村东的苇塘边坐下。", "issues": []},
            "已采纳正文": audit_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("去村东的苇塘"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    scene = next((s for s in session.world.scenes if s.id == "村东苇塘"), None)
    assert scene is not None
    # id 自己算一个别名，审计给的变体名跟在后面；同名的重复项不收。
    assert scene.aliases == ["村东苇塘", "苇塘"]

    # 落盘一致（世界资产 = 磁盘真相）：新场景只带名字，描述是占位。
    on_disk = {
        s["id"]: s
        for s in json.loads((world_root / "scenes.json").read_text(encoding="utf-8"))
    }
    assert on_disk["村东苇塘"]["aliases"] == ["村东苇塘", "苇塘"]
    assert on_disk["村东苇塘"]["perceivable"] == SCENE_PLACEHOLDER


def test_audit_alias_of_registered_scene_is_not_reregistered(session, settings):
    """审计把已注册场景的**别名**单独当 location 给出时，应救回原场景（2026-09-16）。

    ``_resolve_scene`` 原先第一步只认 ``s.id``，别名只在第二个参数（审计的
    ``scene_name``）里被反查。于是审计**只给一个叫法**时（qingshi2 实测里
    ``location_name`` 就是空的），"鱼市"的别名"码头鱼市"会凭空多出一个场景。
    """
    before = {s.id for s in session.world.scenes}
    fish = next(s for s in session.world.scenes if s.id == "鱼市")
    assert "码头鱼市" in fish.aliases  # 别名一直都在，问题只是纠偏时没用上

    audit_out = {
        "location": "码头鱼市",  # 已注册场景的别名，不是新地点
        "scene_name": "",
        "register_scene": True,
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 0,
        "lifecycle": [],
    }
    llm = PrefixKeyLLM(
        {
            # 玩家输入刻意不含任何场景名/别名：让规则侧解不出目的地，
            # 逼 location 走审计那条路（否则 resolve_destination 先救了）。
            "玩家输入：": {
                "prose": "你在附近转了转，最后走到码头边。",
                "summary": "刘星在码头边转了一圈。",
                "actor_questions": [],
            },
            "正文：": {
                "status": "pass",
                "prose": "你在附近转了转，最后走到码头边。",
                "issues": [],
            },
            "已采纳正文": audit_out,
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("在附近转转"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert {s.id for s in session.world.scenes} == before  # 没有多出同义场景
    assert session.ledger.save.player_scene == "鱼市"


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
        "participants": ["刘星", "朱明"],
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
                "participants": ["刘星"],
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
        "participants": ["刘星"],
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

    session.world.player().private_note = "其实是镇长的私生子"
    session.world.player().personal_secrets = "欠了赌债"
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
    assert "主角·刘星" in writer
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

    reference = build_qc_reference(session.world, session.ledger, ["刘星", "朱明"])
    assert "假面骑士" not in reference  # 无秘密时的基线
    session.world.player().personal_secrets = "夜里是假面骑士"
    session.world.player().private_note = "受神之加护，赐予者名雅典娜"
    reference = build_qc_reference(session.world, session.ledger, ["刘星", "朱明"])
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
        "participants": ["刘星", "朱明"],
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
    assert move["known_by"] == ["刘星", "朱明"]
    assert move["location"] == "zhuming_home"
    assert "朱明前往" in move["summary"]
    # 快照更新：朱明从网吧在场名单消失（快照已随离场事件走到未注册的"朱明家"）
    assert "朱明" not in session.ledger.present_at("网吧")
    # 他的已知集含亲历：主事件（被找到聊天）+ 自己的移动
    got = "\n".join(session.ledger.known_set("朱明", "网吧", 0))
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
        "participants": ["刘星", "朱明"],
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


def test_audit_lifecycle_retire_leaves_trace_event(session, settings):
    """审计判永久退场：打 retired 标的同时落一条退场留痕事件（2026-09-12）——
    事件日志是长期记忆比对基准，静默退场会让"他不在了"无史实可引。
    已退场者不重复留痕。"""
    writer_out = {
        "prose": "一声闷响，朱明倒在柜台边，再也没有起来。",
        "summary": "朱明在网吧出事身亡。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "网吧",
        "participants": ["刘星", "朱明"],
        "private": False,
        "delta_minutes": 5,
        "lifecycle": [{"npc_id": "朱明", "status": "retired"}],
    }
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("推了朱明一把"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert session.ledger.save.entities["朱明"].lifecycle == "retired"
    evs = session.ledger.narratives
    main, trace = evs[-2], evs[-1]
    assert "退场" in trace["summary"]
    assert trace["participants"] == ["朱明"]
    assert trace["known_by"] == ["刘星", "朱明"]
    assert "朱明" not in session.ledger.present_at("网吧")

    # 已退场者再被判 retired 不重复留痕。
    audit2 = dict(audit_out, delta_minutes=0, participants=["刘星"])
    llm2 = PrefixKeyLLM(
        {
            "玩家输入：": {
                "prose": "你在网吧坐了很久。",
                "summary": "网吧独坐。",
                "actor_questions": [],
            },
            "正文：": {"status": "pass", "prose": "你在网吧坐了很久。", "issues": []},
            "已采纳正文": audit2,
        }
    )
    runner2 = _runner(session, llm2, settings)
    candidate2 = asyncio.run(runner2.run_turn("继续待着"))
    asyncio.run(runner2.adopt(candidate2.candidate_id))
    evs2 = session.ledger.narratives
    assert "退场" not in evs2[-1]["summary"]  # 最后一轮只有主事件，无第二条留痕
    assert sum(1 for e in evs2 if "退场" in (e.get("summary") or "")) == 1


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
        "participants": ["刘星", "王蓉"],
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
    assert session.ledger.save.last_settlement["source"] == "clock_to"


def _clock_audit_case(session, settings, audit_out, player_input="继续待着"):
    """跑一轮 chat_act 并采纳，返回结算留痕（审计结果由 audit_out 指定）。"""
    writer_out = {
        "prose": "时间在不知不觉里过去。",
        "summary": "时间流逝。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn(player_input))
    asyncio.run(runner.adopt(candidate.candidate_id))
    return session.ledger.save.last_settlement, candidate


def test_time_is_settled_by_audit_only(session, settings):
    """时钟推进的唯一来源是审计（2026-09-16 去规则化）。

    以前"第二天去学校"是规则对钟 + 审计估时长走「或」关系（两条链互不叠加）；
    现在规则侧不再产出任何时间，玩家没给明确时刻时，落点完全由审计的 delta 决定。
    """
    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 480,
        "lifecycle": [],
    }
    settlement, _ = _clock_audit_case(session, settings, audit_out, player_input="第二天去学校")

    assert session.ledger.save.clock == "2026-07-14T16:00:00"  # 08:00 + 480
    assert settlement["source"] == "audit"
    assert settlement["delta_applied"] == 480
    assert "settle_rule" not in settlement
    assert "delta_rule" not in settlement


class _AuditBoomLLM(PrefixKeyLLM):
    """审计那一笔必炸，用来验证"审计失败时采纳不回滚、时钟不动"。"""

    async def complete_json(self, messages, schema, **kwargs):
        if getattr(schema, "__name__", "") == "AuditOutput":
            raise RuntimeError("audit down")
        return await super().complete_json(messages, schema, **kwargs)


def test_audit_failure_does_not_roll_back_or_advance(session, settings):
    """审计调用失败 → 采纳照旧成功、时钟原地不动，但失败必须留痕（不静默）。

    去规则化后规则侧不再兜底时间，所以正确行为就是「什么都不推进」——
    误推进（玩家没明说时间却跳掉几个小时）比不推进严重得多。
    """
    writer_out = {
        "prose": "第二天早上，你醒了。",
        "summary": "第二天早上。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    llm = _AuditBoomLLM({"玩家输入：": writer_out, "正文：": qc_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("第二天去学校"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert session.ledger.save.clock == "2026-07-14T08:00:00"
    assert session.ledger.events[-1]["source"] == "turn"  # 采纳仍然落地
    s = session.ledger.save.last_settlement
    assert s["source"] == "none"
    assert "audit down" in (s["audit_error"] or "")


def test_audit_negative_delta_does_not_rewind_clock(session, settings):
    """审计吐负数不能让时钟倒流（原实现 min(x, 960) 不挡负数）。"""
    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": -30,
        "lifecycle": [],
    }
    settlement, _ = _clock_audit_case(session, settings, audit_out)

    assert session.ledger.save.clock == "2026-07-14T08:00:00"
    assert settlement["audit_negative"] is True
    assert settlement["delta_audit"] == 0
    assert settlement["delta_applied"] == 0
    assert settlement["source"] == "none"


def test_audit_delta_is_capped_by_fuse(session, settings):
    """审计估时长的 24h 保险丝仍然生效，且被截断要留痕（原实现静默截断）。"""
    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 3000,
        "lifecycle": [],
    }
    settlement, _ = _clock_audit_case(session, settings, audit_out)

    assert settlement["delta_audit_raw"] == 3000
    assert settlement["delta_audit"] == 1440
    assert settlement["audit_capped"] is True
    assert session.ledger.save.clock == "2026-07-15T08:00:00"  # 08:00 + 24h


def test_clock_to_earlier_than_now_is_rejected(session, settings):
    """审计把日期填错（早于当前时钟）→ 拒绝对钟、退回估时长，并留下原因。"""
    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 15,
        "clock_to": "2026-07-01T10:00:00",
        "lifecycle": [],
    }
    settlement, _ = _clock_audit_case(session, settings, audit_out)

    assert session.ledger.save.clock == "2026-07-14T08:15:00"
    assert settlement["source"] == "audit"
    assert settlement["clock_to_rejected"] == "早于当前时钟，疑似日期填错"


def test_clock_to_bad_format_is_rejected(session, settings):
    """clock_to 格式坏 → 拒绝对钟、退回估时长，并留下原因（原先静默 pass）。"""
    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 15,
        "clock_to": "明天早上",
        "lifecycle": [],
    }
    settlement, _ = _clock_audit_case(session, settings, audit_out)

    assert session.ledger.save.clock == "2026-07-14T08:15:00"
    assert settlement["clock_to_rejected"] == "时间格式无法解析"


def test_writer_gets_human_readable_rule_brief(session, fake_llm, settings):
    """规则段预结算进 prompt 的是人话，不再是 raw dict repr。

    旧实现贴的是 `规则段预结算：{'route': 'jump', 'delta_minutes': 720}`——
    编剧只能靠猜字段名，而 route/delta 这类引擎内部词汇对它毫无意义。
    2026-09-16 起规则段只剩移动，所以**绝对时刻不再进正文约束**（时间交审计）。
    """
    runner = _runner(session, fake_llm, settings)
    asyncio.run(runner.run_turn("第二天去鱼市"))
    blob = json.dumps(fake_llm.calls, ensure_ascii=False)

    assert "规则段预结算" in blob
    assert "鱼市" in blob  # 目的地进了
    assert "2026-07-15T08:00:00" not in blob  # 时间不再预结算
    assert "'route'" not in blob
    assert "'delta_minutes'" not in blob
    assert "'settle_to'" not in blob


def test_audit_gets_player_input_without_rule_hint(session, settings):
    """审计必须拿到玩家输入（"玩家给了时间就照办"全靠它），且不再有规则预告。

    去规则化前这里会塞 settle_hint（"时钟已定到 X，这段不要再估"）；现在规则侧
    不产时间，审计是唯一结算方，任何"别再估"的提示都是错的。
    """
    writer_out = {
        "prose": "第二天早上，你醒了。",
        "summary": "第二天早上。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 0,
        "lifecycle": [],
    }
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("第二天去学校"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    audit_calls = [
        call
        for call in llm.calls
        if call["messages"] and "世界审计" in (call["messages"][0].get("content") or "")
    ]
    assert audit_calls
    last = audit_calls[-1]
    system = last["messages"][0]["content"]
    user_blob = " ".join(m.get("content", "") for m in last["messages"] if m["role"] == "user")

    assert "第二天去学校" in user_blob  # 玩家输入随审计下去
    assert "不要再估" not in system
    assert "settle_hint" not in system
    assert "自行估计" in system  # 没给时刻就自行估时长


def test_settlement_record_is_persisted_to_save_json(session, world_root, settings):
    """结算留痕必须真的落盘（顶栏时钟 hover 读的就是它）。"""
    import json

    audit_out = {
        "location": "家",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 30,
        "lifecycle": [],
    }
    _clock_audit_case(session, settings, audit_out)

    raw = json.loads((world_root / "save.json").read_text(encoding="utf-8"))
    s = raw["last_settlement"]
    assert s["clock_before"] == "2026-07-14T08:00:00"
    assert s["clock_after"] == "2026-07-14T08:30:00"
    assert s["source"] == "audit"
    assert s["delta_applied"] == 30


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


def _stage_presence(session, scene: str, who: list[str]) -> None:
    """造一条带位置的事件，让人被 ``present_at`` 认定在该场景（测试专用布景）。"""
    session.ledger.append(
        {
            "id": session.ledger.allocate_event_id(),
            "kind": "narrative",
            "at": session.ledger.save.clock or "2026-07-14T08:00:00",
            "location": scene,
            "participants": who,
            "known_by": None,
            "body": "",
            "summary": "测试布景",
            "player_input": None,
            "source": "test",
        }
    )


def test_lore_subject_injected_when_owner_present(session):
    """归属条目（subject）：归属角色**在场**即注入，不靠正文点名——
    "一个 NPC 的信息 = 卡 + 归属条目"才齐全。不占关键词触发名额。"""
    from app.core.workorder import build_work_order
    from app.world.models import LoreEntry

    session.ledger.world.lorebook.append(
        LoreEntry(id="zhu_migraine", keywords=[], body="朱文武常年偏头痛。", subject="朱明")
    )
    # 本人不在场（也没人点到名）：归属条目不入——"你爸的病"这种旁敲侧击够不着它
    order = build_work_order("writer", session.world, session.ledger, "主街")
    assert "朱文武常年偏头痛" not in order

    _stage_presence(session, "网吧", ["刘星", "朱明"])
    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "【归属·朱明】" in order
    assert "朱文武常年偏头痛" in order

    # 关键词名额塞满也挤不掉归属条目：两条通道各自计数
    for i in range(6):
        session.ledger.world.lorebook.append(
            LoreEntry(id=f"lore_kw{i}", keywords=[f"线索{i}"], body=f"背景{i}")
        )
    from app.rules.lorebook import merge_active_lore

    session.ledger.save.active_lore_ids = merge_active_lore(
        [], [f"lore_kw{i}" for i in range(6)]
    )
    assert len(session.ledger.save.active_lore_ids) == 5
    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "朱文武常年偏头痛" in order
    assert "背景5" not in order  # 第 6 条关键词条目仍被 5 条上限挡住


def test_lore_subject_cap_two_per_character(session):
    """每人归属条目上限 2 条：超出的按录入顺序截断（重要的往上放）。"""
    from app.core.workorder import build_work_order
    from app.rules.lorebook import subject_hit_ids
    from app.world.models import LoreEntry

    for i in range(3):
        session.ledger.world.lorebook.append(
            LoreEntry(id=f"zhu_{i}", keywords=[], body=f"朱明的归属背景{i}", subject="朱明")
        )
    _stage_presence(session, "网吧", ["刘星", "朱明"])

    assert subject_hit_ids(session.world, ["朱明", "王蓉"]) == ["zhu_0", "zhu_1"]
    assert subject_hit_ids(session.world, ["王蓉"]) == []  # 本人不在场 → 一条都不进

    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "朱明的归属背景0" in order
    assert "朱明的归属背景1" in order
    assert "朱明的归属背景2" not in order


# ---------------------------------------------------------------------------
# 角色状态 · 长期事实：落地（REQ 〇章；Step 2，2026-09-14）
# ---------------------------------------------------------------------------

def _state_turn(session, settings, audit_out, player_input="继续待着"):
    """跑一轮并采纳（审计输出由 audit_out 指定），返回 Candidate。"""
    writer_out = {
        "prose": "时间在不知不觉里过去。",
        "summary": "时间流逝。",
        "actor_questions": [],
    }
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    llm = PrefixKeyLLM({"玩家输入：": writer_out, "正文：": qc_out, "已采纳正文": audit_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn(player_input))
    asyncio.run(runner.adopt(candidate.candidate_id))
    return candidate


def _base_audit(**extra):
    audit = {
        "location": "主街",
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 0,
        "lifecycle": [],
    }
    audit.update(extra)
    return audit


def _seed_state(session, npc_id: str, text: str, *, until: str = "") -> StateItem:
    runtime = session.ledger.save.entities.setdefault(npc_id, EntityRuntime())
    item = StateItem(text=text, until=until)
    runtime.states.append(item)
    return item


def test_audit_state_add_lands_with_source_event(session, settings):
    """审计说"正文里他获得了某状态" → 落地 + 一条可追溯的记账史实。"""
    _state_turn(
        session,
        settings,
        _base_audit(
            state_add=[{"npc_id": "刘星", "text": "普通刀剑伤不了他", "public": True}]
        ),
    )
    states = session.ledger.save.entities["刘星"].states
    assert [s.text for s in states] == ["普通刀剑伤不了他"]
    assert states[0].public is True
    assert states[0].source_event  # 指得着才叫可追溯（撤销靠它）

    ev = session.ledger.by_id[states[0].source_event]
    assert ev["body"] == ""  # 记账条目不是叙述
    assert ev["location"] is None  # 不给位置语义，免得污染 where_is
    assert ev["summary"] == "刘星获得状态：普通刀剑伤不了他"
    assert ev["participants"] == ["刘星"]
    assert ev["known_by"] == ["刘星"]
    assert ev["source"] == "state"

    assert session.ledger.save.last_state_change["added"][0]["id"] == states[0].id


def test_state_add_is_deduped_across_turns(session, settings):
    """审计每打一架就把同一条再报一遍 → 不能重复入库（否则六格被一个事实吃光）。"""
    audit = _base_audit(state_add=[{"npc_id": "刘星", "text": "沐浴龙血"}])
    _state_turn(session, settings, audit)
    _state_turn(session, settings, audit)
    assert [s.text for s in session.ledger.save.entities["刘星"].states] == ["沐浴龙血"]
    reasons = [s["reason"] for s in session.ledger.save.last_state_change["skipped"]]
    assert "已有同一状态" in reasons


def test_state_add_unknown_npc_is_skipped(session, settings):
    _state_turn(session, settings, _base_audit(state_add=[{"npc_id": "张三", "text": "怕水"}]))
    assert "张三" not in session.ledger.save.entities
    assert session.ledger.save.last_state_change["skipped"][0]["reason"] == "不在人物表"


def test_state_add_is_one_per_character_per_turn(session, settings):
    """限的是"同一角色一条"——防的是给同一个人一口气编一串。"""
    _state_turn(
        session,
        settings,
        _base_audit(
            state_add=[
                {"npc_id": "刘星", "text": "断了一条手臂"},
                {"npc_id": "刘星", "text": "中了蛇毒"},
            ]
        ),
    )
    assert [s.text for s in session.ledger.save.entities["刘星"].states] == ["断了一条手臂"]


def test_state_add_allows_several_characters_in_one_turn(session, settings):
    """龙血溅到三个人身上是该落三条的——所以全局**不设**硬顶。"""
    _state_turn(
        session,
        settings,
        _base_audit(
            state_add=[
                {"npc_id": "刘星", "text": "龙血浸体"},
                {"npc_id": "朱明", "text": "龙血溅身"},
            ]
        ),
    )
    assert len(session.ledger.save.entities["刘星"].states) == 1
    assert len(session.ledger.save.entities["朱明"].states) == 1


def test_state_add_blank_text_is_ignored(session, settings):
    _state_turn(
        session, settings, _base_audit(state_add=[{"npc_id": "刘星", "text": "   "}])
    )
    assert "刘星" not in session.ledger.save.entities


def test_state_text_is_truncated(session, settings):
    """提示词要求 ≤STATE_TEXT_MAX 字；模型不听话时按上限截断，别把整段设定塞进常驻块。"""
    _state_turn(
        session, settings, _base_audit(state_add=[{"npc_id": "刘星", "text": "很长" * 40}])
    )
    assert len(session.ledger.save.entities["刘星"].states[0].text) == STATE_TEXT_MAX


def test_state_remove_by_id(session, settings):
    """移除按 id——审计看不见 id 就只能按原文猜，"骨裂"与"左臂骨裂"必有一场误伤。"""
    item = _seed_state(session, "刘星", "左臂骨裂")
    _state_turn(session, settings, _base_audit(state_remove=[item.id]))
    assert session.ledger.save.entities["刘星"].states == []
    removed = session.ledger.save.last_state_change["removed"][0]
    assert removed["text"] == "左臂骨裂"
    assert session.ledger.by_id[removed["event_id"]]["summary"] == "刘星状态结束：左臂骨裂"


def test_state_remove_accepts_dict_shape(session, settings):
    """宽松解析：审计偶尔给 {"state_id": …} 而不是裸字符串，不该让整单结算失败。"""
    item = _seed_state(session, "刘星", "左臂骨裂")
    _state_turn(session, settings, _base_audit(state_remove=[{"state_id": item.id}]))
    assert session.ledger.save.entities["刘星"].states == []


def test_state_remove_unknown_id_is_skipped(session, settings):
    _state_turn(session, settings, _base_audit(state_remove=["st_不存在"]))
    assert session.ledger.save.last_state_change["skipped"][0]["reason"] == "找不到该状态 id"


def test_state_remove_skips_already_expired_same_turn(session, settings):
    """到期与移除撞在同一回合：① 已宣布它结束（写 ``expired_at`` + 落事件），
    ③ 再 pop 一次会**重复记一条「状态结束」**，还把条目从存档抹掉——「失效不删除」
    是留给玩家撤销入口的，删了就撤不成。所以 ③ 跳过，记 skipped。
    """
    item = _seed_state(session, "刘星", "病倒", until="2026-07-13T08:00:00")  # 早于当前时钟
    _state_turn(session, settings, _base_audit(state_remove=[item.id]))
    stored = session.ledger.save.entities["刘星"].states
    assert [s.id for s in stored] == [item.id]  # 条目还在（没被移除抹掉）
    assert stored[0].expired_at  # 但已标记失效
    change = session.ledger.save.last_state_change
    assert change["removed"] == []
    assert change["skipped"][0]["reason"] == "已到期（到期清算已处理）"
    ends = [e for e in session.ledger.events if "状态结束" in (e.get("summary") or "")]
    assert len(ends) == 1  # 只记一次账，不重复


def test_until_expiry_marks_the_state_without_deleting_it(session, settings):
    """到期 = 标记失效，**不删条目**（2026-09-14 晚改）。

    旧语义是到点 pop 掉。问题是 ``until`` 是**审计单方面**给的（玩家没有异议
    渠道），静默删除等于把"审计写了个期限"变成一条玩家事后既查不到、也撤不掉
    的操作。现在只写 ``expired_at`` + 落一条记账事件，条目留在存档里等玩家处置。
    """
    item = _seed_state(session, "刘星", "病倒", until="2026-07-13T08:00:00")  # 早于当前时钟
    _state_turn(session, settings, _base_audit())
    stored = session.ledger.save.entities["刘星"].states
    assert [s.id for s in stored] == [item.id]  # 条目还在
    assert stored[0].expired_at  # 但标了失效时刻
    assert session.ledger.save.last_state_change["expired"][0]["text"] == "病倒"

    # 失效只记账一次：再跑几轮不会重复落"状态结束"事件（expired_at 兼作哨兵）。
    _state_turn(session, settings, _base_audit())
    _state_turn(session, settings, _base_audit())
    assert session.ledger.save.last_state_change["expired"] == []
    ends = [e for e in session.ledger.events if "状态结束" in (e.get("summary") or "")]
    assert len(ends) == 1


def test_expired_state_stays_revocable(session, settings):
    """失效之后玩家仍撤得掉——这正是"标记而不删"的全部目的。"""
    item = _seed_state(session, "刘星", "病倒", until="2026-07-13T08:00:00")
    _state_turn(session, settings, _base_audit())
    assert _revoke(session, settings, item.id) is True
    assert session.ledger.save.entities["刘星"].states == []
    assert session.ledger.save.last_state_change["revoked"][0]["id"] == item.id


def test_audit_supplied_until_lands_and_expires(session, settings):
    """审计给出期限（"病倒三天"）→ 落地成 until，时钟过后由规则侧标记失效。

    这是"只收长期事实"的配套闸门：有明确终点的（病 / 骨裂 / 中毒）按点自愈，
    而不是永久粘在提示词里。但**自愈 = 停止注入，不是把条目抹掉**。
    """
    import datetime as dt

    base = dt.datetime.fromisoformat(session.ledger.save.clock)
    until = (base + dt.timedelta(hours=1)).replace(microsecond=0).isoformat()
    _state_turn(
        session,
        settings,
        _base_audit(state_add=[{"npc_id": "刘星", "text": "病倒", "until": until}]),
    )
    assert session.ledger.save.entities["刘星"].states[0].until == until

    # 下一轮时钟推 2 小时 → 越过 until，到期清算标记失效（条目留下）。
    _state_turn(session, settings, _base_audit(delta_minutes=120))
    stored = session.ledger.save.entities["刘星"].states
    assert [s.text for s in stored] == ["病倒"]
    assert stored[0].expired_at
    assert session.ledger.save.last_state_change["expired"][0]["text"] == "病倒"


def test_state_change_buckets_all_carry_public_for_the_left_column(session, settings):
    """六个桶的条目都带 ``public``（2026-09-16）：左栏「本回合变化」据此挂「秘」。

    为什么不能让前端自己去面板里查：左栏列的是**这次刚动的**条目，而其中"已结束 /
    已撤销 / 已到期"的那几条在 ``state_view`` 里已经查不到了（撤掉的从存档里没了、
    到期的被过滤进 expired 桶、被审计移除的直接不在），留痕里这一份是唯一数据源。
    所以字段必须跟着桶走，而不是只在 added 上挂一个。

    只钉"有字段"，不钉"谁来标"——前端 ``public === false`` 的判据与渲染由 jsdom
    实测覆盖（pytest 里没有浏览器，``===`` 与 ``!`` 的差别正是那里最容易写错的）。
    """
    # ① added / ③ removed（审计两路，同一回合）
    gone = _seed_state(session, "刘星", "左臂骨裂")
    _state_turn(
        session,
        settings,
        _base_audit(
            state_add=[{"npc_id": "刘星", "text": "普通刀剑伤不了他", "public": True}],
            state_remove=[gone.id],
        ),
    )
    change = session.ledger.save.last_state_change
    assert change["added"][0]["public"] is True
    assert change["removed"][0]["public"] is False  # _seed_state 默认不公开

    # ① 到期清算（规则侧）
    _seed_state(session, "刘星", "病倒", until="2026-07-13T08:00:00")  # 早于当前时钟
    _state_turn(session, settings, _base_audit())
    assert session.ledger.save.last_state_change["expired"][0]["public"] is False

    # 玩家三路：add / revise / revoke 也记在同一份留痕里
    runner = _runner(session, FakeLLM({"*": {}}), settings)
    item = runner.transaction.add_state("朱明", "其实色盲")
    assert session.ledger.save.last_state_change["added"][-1]["public"] is False
    runner.transaction.revise_state(item.id, public=True)
    # 修订后记的是**修订后**的可见性——左栏要标的是"这条现在是不是秘"。
    assert session.ledger.save.last_state_change["revised"][-1]["public"] is True
    assert runner.transaction.revoke_state(item.id) is True
    assert session.ledger.save.last_state_change["revoked"][-1]["public"] is True


def test_player_can_add_a_state(session, settings):
    """导演窗口手动加状态：落条目 + 记账事件 + 进 last_state_change.added。

    **不做语义门槛**——审计那套"只收长期事实"是防它自己过度发挥的；玩家已经
    拍板的事不该再被机器拦（"她其实是色盲"看着像前史，但玩家说了算）。进 added
    是为了左侧栏立刻给出撤销入口：刚加完就能反悔。
    """
    _seed_change(session)
    runner = _runner(session, FakeLLM({"*": {}}), settings)
    item = runner.transaction.add_state("朱明", "左腿摔伤，走路瘸")

    stored = session.ledger.save.entities["朱明"].states
    assert [s.id for s in stored] == [item.id]
    assert item.since == session.ledger.save.clock
    assert item.expired_at == "" and item.until == ""

    ev = session.ledger.by_id[item.source_event]
    assert ev["source"] == "state"
    assert ev["summary"] == "朱明获得状态：左腿摔伤，走路瘸（玩家设定）"
    assert ev["participants"] == ["朱明"] and ev["body"] == "" and ev["location"] is None

    # added 里带 public：左侧栏据它挂「秘」（2026-09-16）——玩家刚新增完一条，
    # 人就在左栏，那里看不到「秘」就会疑惑 NPC 为什么不接话。
    assert session.ledger.save.last_state_change["added"] == [
        {
            "npc_id": "朱明",
            "id": item.id,
            "text": item.text,
            "event_id": item.source_event,
            "public": False,
        }
    ]
    # 撤销入口就绪：刚加的这条能一键反悔。
    assert _revoke(session, settings, item.id) is True


def test_player_add_state_rejects_bad_input(session, settings):
    """守卫只管格式与归属（空文本 / 不在人物表 / 重复），不管语义。"""
    runner = _runner(session, FakeLLM({"*": {}}), settings)
    with pytest.raises(ValueError):
        runner.transaction.add_state("朱明", "   ")
    with pytest.raises(ValueError):
        runner.transaction.add_state("查无此人", "腿伤")
    runner.transaction.add_state("朱明", "左腿摔伤")
    with pytest.raises(ValueError):
        runner.transaction.add_state("朱明", "左腿摔伤")


def test_player_add_state_clips_and_ignores_expired_for_dedupe(session, settings):
    """30 字截断；去重只比**未失效**的条目——一条已到期的"病倒"不该挡着下一次。"""
    runner = _runner(session, FakeLLM({"*": {}}), settings)
    item = runner.transaction.add_state("朱明", "伤" * 40)
    assert item.text == "伤" * STATE_TEXT_MAX

    # until 走同一条清洗：人话期限（"三天后"）当没给，别让它永不到期。
    poisoned = runner.transaction.add_state("朱明", "中毒", until="三天后")
    assert poisoned.until == ""

    item.expired_at = session.ledger.save.clock
    again = runner.transaction.add_state("朱明", "伤" * 40)  # 不抛
    assert again.id != item.id


def test_player_add_state_without_a_change_trace_still_lands(session, settings):
    """``last_state_change`` 为 None（从没采纳过任何回合）时不崩，只是不记 added。"""
    runner = _runner(session, FakeLLM({"*": {}}), settings)
    assert session.ledger.save.last_state_change is None
    item = runner.transaction.add_state("朱明", "左腿摔伤")
    assert session.ledger.save.last_state_change is None
    assert session.ledger.save.entities["朱明"].states[0].id == item.id


def test_unparseable_until_is_dropped(session, settings):
    """审计写人话（"三天后"）→ 当没给。

    直接存进去的后果是这条状态**永远不到期**（`_is_expired` 解析失败一律当永不
    到期）——一条本该自愈的"病倒"永久粘在提示词里，比不给期限更糟。
    """
    _state_turn(
        session,
        settings,
        _base_audit(state_add=[{"npc_id": "刘星", "text": "病倒", "until": "三天后"}]),
    )
    assert session.ledger.save.entities["刘星"].states[0].until == ""


def test_states_survive_audit_failure(session, settings):
    """审计炸了 → 状态一动不动（不能因为审计失败就丢玩家的状态）。"""
    _seed_state(session, "刘星", "沐浴龙血")
    writer_out = {"prose": "你继续待着。", "summary": "待着。", "actor_questions": []}
    qc_out = {"status": "pass", "prose": writer_out["prose"], "issues": []}
    llm = _AuditBoomLLM({"玩家输入：": writer_out, "正文：": qc_out})
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("继续待着"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert [s.text for s in session.ledger.save.entities["刘星"].states] == ["沐浴龙血"]
    change = session.ledger.save.last_state_change
    assert change["added"] == [] and change["removed"] == []


# ---------------------------------------------------------------------------
# 角色状态 · 长期事实：玩家撤销（REQ 〇章；Step 2b，2026-09-14）
# ---------------------------------------------------------------------------

def _revoke(session, settings, state_id: str) -> bool:
    runner = _runner(session, FakeLLM({"*": {}}), settings)
    return runner.transaction.revoke_state(state_id)


def _seed_change(session, **extra) -> dict:
    """造一份"上一回合的变更留痕"——撤销把 revoked 记在它上面（真实场景里它总是存在）。

    ``last_state_change`` 只可能是 None 的两种情况：从没采纳过任何回合、刚重置过；
    那两种情况下面板也没有"本回合变化"可标灰，所以撤销只落记账事件（另测）。
    """
    change = {
        "turn_id": "turn_probe",
        "at": session.ledger.save.clock,
        "added": [],
        "removed": [],
        "expired": [],
        "skipped": [],
        "revoked": [],
    }
    change.update(extra)
    session.ledger.save.last_state_change = change
    return change


def test_revoke_removes_state_and_records_counter_event(session, settings):
    """撤销 = 删条目 + 一条对冲记账事件（正文与事件日志不动，改的是运行态）。"""
    item = _seed_state(session, "刘星", "普通刀剑伤不了他")
    _seed_change(session)
    session.ledger.persist_save()
    assert _revoke(session, settings, item.id) is True

    assert session.ledger.save.entities["刘星"].states == []
    revoked = session.ledger.save.last_state_change["revoked"][0]
    assert revoked["id"] == item.id and revoked["npc_id"] == "刘星"

    ev = session.ledger.by_id[revoked["event_id"]]
    assert ev["summary"] == "刘星状态撤销：普通刀剑伤不了他（玩家撤销）"
    assert ev["source"] == "state"
    assert ev["body"] == "" and ev["location"] is None
    assert ev["participants"] == ["刘星"]
    assert ev["known_by"] == ["刘星"]  # 记账事件的主体只有本人 + 玩家（无玩家时只有本人）


def test_revoke_survives_reopen(session, settings):
    """撤销是落盘的：重开存档后条目不复现、留痕还在。"""
    item = _seed_state(session, "刘星", "左臂骨裂")
    _seed_change(session)
    session.ledger.persist_save()
    _revoke(session, settings, item.id)

    reopened = open_session(session.root)
    assert "刘星" not in reopened.ledger.save.entities or (
        reopened.ledger.save.entities["刘星"].states == []
    )
    assert reopened.ledger.save.last_state_change["revoked"][0]["id"] == item.id
    assert any(e.get("source") == "state" for e in reopened.ledger.events)


def test_revoke_records_into_an_old_change_without_the_revoked_key(session, settings):
    """老存档的 last_state_change 没有 revoked 键 → setdefault 兜住，不能崩。"""
    item = _seed_state(session, "刘星", "沐浴龙血")
    session.ledger.save.last_state_change = {
        "turn_id": "turn_old",
        "at": session.ledger.save.clock,
        "added": [],
        "removed": [],
        "expired": [],
        "skipped": [],
    }
    session.ledger.persist_save()
    assert _revoke(session, settings, item.id) is True
    assert session.ledger.save.last_state_change["revoked"][0]["id"] == item.id


def test_revoke_unknown_id_is_a_noop(session, settings):
    """找不到就返回 False（API 据此答 404），不能静默成功、也不能落下半条留痕。"""
    _seed_state(session, "刘星", "沐浴龙血")
    _seed_change(session)
    session.ledger.persist_save()
    assert _revoke(session, settings, "st_不存在") is False
    assert [s.text for s in session.ledger.save.entities["刘星"].states] == ["沐浴龙血"]
    assert session.ledger.save.last_state_change["revoked"] == []
    assert not [e for e in session.ledger.events if e.get("source") == "state"]


def test_revoke_is_not_repeatable(session, settings):
    """第二次撤同一条 → False（条目已经不在，别再落一条重复的"撤销"史实）。"""
    item = _seed_state(session, "刘星", "沐浴龙血")
    _seed_change(session)
    session.ledger.persist_save()
    assert _revoke(session, settings, item.id) is True
    assert _revoke(session, settings, item.id) is False
    assert len(session.ledger.save.last_state_change["revoked"]) == 1


def test_revoked_state_leaves_the_writer_order(session, settings):
    """撤销的实质效果：编剧此后不再认为他有（提示词里那一行消失）。"""
    from app.core.workorder import build_work_order

    item = _seed_state(session, "刘星", "普通刀剑伤不了他")
    session.ledger.persist_save()
    assert "普通刀剑伤不了他" in build_work_order(
        "writer", session.world, session.ledger, session.ledger.current_scene()
    )
    _revoke(session, settings, item.id)
    assert "普通刀剑伤不了他" not in build_work_order(
        "writer", session.world, session.ledger, session.ledger.current_scene()
    )


def test_revoke_works_without_any_last_state_change(session, settings):
    """存档里 last_state_change 为 None 时撤销也不能崩（老存档 / 刚重置过）。"""
    item = _seed_state(session, "刘星", "沐浴龙血")
    session.ledger.save.last_state_change = None
    session.ledger.persist_save()
    assert _revoke(session, settings, item.id) is True
    assert session.ledger.save.entities["刘星"].states == []
