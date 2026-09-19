"""NPC 落卡机制（2026-09-19）：未落卡的确定人物 = 有键无卡。

边界（与玩家敲定的唯一口径）：**有名字 + 与玩家有往来累计 ≥2 轮** → 引擎授**键**
（进 ``save.unfiled``）。没名字的即兴角色（酒保 / 伙计）审计侧就不收，永远不计数。

键是**运行态**（随世界重置清零），卡是**资产**（``npcs/*.json``）——两层分开，
所以"未落卡"这个中间态必须自洽：他此刻该被看见（进在场名单、进提示词），
但引擎不能替他编档案。
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

from app.core.workorder import UNFILED_CARD_CAP, build_work_order
from app.ledger.save import EntityRuntime
from tests.test_turn import PrefixKeyLLM, _place, _runner


def _audit(featured: list[str], participants: list[str]) -> dict:
    return {
        "location": "主街",
        "scene_name": "",
        "register_scene": False,
        "participants": participants,
        "featured": featured,
        "private": False,
        "delta_minutes": 0,
        "lifecycle": [],
    }


def _adopt_two_rounds(
    session, settings, featured: list[str], participants: list[str]
) -> None:
    """跑两轮（正文→QC→审计）并采纳。"""
    llm = PrefixKeyLLM(
        {
            "玩家输入：": {
                "prose": "你走进酒馆，跟酒保攀谈起来。",
                "summary": "刘星在酒馆跟酒保攀谈。",
                "actor_questions": [],
            },
            "正文：": {
                "status": "pass",
                "prose": "你走进酒馆，跟酒保攀谈起来。",
                "issues": [],
            },
            "已采纳正文": _audit(featured, participants),
        }
    )
    runner = _runner(session, llm, settings)
    for text in ("进酒馆", "跟酒保聊天"):
        candidate = asyncio.run(runner.run_turn(text))
        asyncio.run(runner.adopt(candidate.candidate_id))


@contextmanager
def _api(tmp_path):
    """开档 + 起 API（落卡要过 check_assets + reload_world，得走真实的写路径）。

    必须进入 ``with`` 才拿到会话——``TestClient`` 的 lifespan 在 ``__enter__``
    里才跑，提前发请求会撞上"app.state 还没装配"。
    """
    from tests.test_api import _make_client

    with _make_client(tmp_path) as client:
        sid = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()["sid"]
        yield client, sid, client.app.state.sessions[sid]


# --------------------------------------------------------------------------
# 计数与授键
# --------------------------------------------------------------------------

def test_named_featured_grants_key_at_threshold(session, settings) -> None:
    """有名字的角色出场累计 2 轮 → 授键。第 1 轮只是计数，不授键。"""
    llm = PrefixKeyLLM(
        {
            "玩家输入：": {
                "prose": "你走进酒馆，跟酒保攀谈起来。",
                "summary": "刘星在酒馆跟酒保攀谈。",
                "actor_questions": [],
            },
            "正文：": {"status": "pass", "prose": "你走进酒馆，跟酒保攀谈起来。", "issues": []},
            "已采纳正文": _audit(["卢克"], ["刘星", "卢克"]),
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn("进酒馆"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert session.ledger.save.featured_counts["卢克"] == 1
    assert session.ledger.save.unfiled == []  # 还没到阈值
    # 出场 ≠ 在场：他这一轮被写过，但没有键也没有卡 → 还进不了在场名单。
    assert "卢克" not in session.ledger.present_at("主街")

    candidate = asyncio.run(runner.run_turn("跟卢克聊两句"))
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert session.ledger.save.featured_counts["卢克"] == 2
    assert session.ledger.save.unfiled == ["卢克"]  # 授键
    # 授键之后他才进在场名单（域 = 人物表 ∪ 未落卡者）。
    assert "卢克" in session.ledger.present_at("主街")


def test_unnamed_featured_is_not_counted(session, settings) -> None:
    """没名字的即兴角色审计侧就不收 → 永远不计数（"没名字就无从落卡"）。"""
    _adopt_two_rounds(session, settings, featured=[], participants=["刘星", "酒保"])
    assert session.ledger.save.featured_counts == {}
    assert session.ledger.save.unfiled == []


def test_player_featured_is_ignored(session, settings) -> None:
    """主角自己不算"出场角色"（他恒在参与者名单里，不该给自己授键）。"""
    _adopt_two_rounds(session, settings, featured=["刘星"], participants=["刘星"])
    assert "刘星" not in session.ledger.save.unfiled
    assert session.ledger.save.featured_counts == {}


def test_existing_card_never_enters_unfiled(session, settings) -> None:
    """有卡的（真正的）NPC 不进 unfiled——他已有档案，不需要"落卡"。"""
    player = session.world.player_name()
    npc = next(n for n in session.world.npcs if n != player)
    _adopt_two_rounds(session, settings, featured=[npc], participants=[player, npc])
    assert session.ledger.save.unfiled == []
    assert npc not in session.ledger.save.featured_counts


def test_retired_name_loses_key(session, settings) -> None:
    """已退场者不该被授键；已在 unfiled 里的也被清掉。"""
    session.ledger.save.unfiled.append("卢克")
    session.ledger.save.featured_counts["卢克"] = 1
    session.ledger.save.entities["卢克"] = EntityRuntime(lifecycle="retired")
    _adopt_two_rounds(session, settings, featured=["卢克"], participants=["刘星", "卢克"])
    assert session.ledger.save.unfiled == []
    assert "卢克" not in session.ledger.save.featured_counts


# --------------------------------------------------------------------------
# 注入：看见他，但不许替他编档案
# --------------------------------------------------------------------------

def test_unfiled_is_present_but_flagged_temporary(session) -> None:
    """未落卡者进在场名单，但提示词必须显式声明"引擎没有他的档案"。"""
    scene = session.ledger.current_scene()
    session.ledger.save.unfiled.append("卢克")
    _place(session, "卢克", scene)

    out = build_work_order("writer", session.world, session.ledger, scene)
    assert "在场临时角色：卢克" in out
    assert "有名字但引擎没有档案" in out
    # 不能把他当有卡 NPC 渲染——那会凭空长出外貌 / 人格。
    assert "[卢克]" not in out


def test_unfiled_list_is_capped_with_explicit_tail(session) -> None:
    """人多了按上限截断，**必须显式说明**（静默消失会让编剧以为没别人）。"""
    scene = session.ledger.current_scene()
    names = [f"路人甲{i}" for i in range(UNFILED_CARD_CAP + 2)]
    for name in names:
        session.ledger.save.unfiled.append(name)
        _place(session, name, scene)

    out = build_work_order("writer", session.world, session.ledger, scene)
    # 截断只针对「临时角色」块本身（在场名单是位置事实，不套这个 cap）。
    block = out.split("在场临时角色：", 1)[1].split("\n", 1)[0]
    assert "另有 2 个未列出" in block
    assert names[0] in block
    assert names[-1] not in block


def test_unfiled_state_is_inspectable(tmp_path) -> None:
    """`/state` 要把键的现状透出来（含他在哪、是否在场）——否则玩家无从下手。"""
    with _api(tmp_path) as (client, sid, session):
        scene = session.ledger.current_scene()
        session.ledger.save.unfiled.extend(["卢克", "米娅"])
        _place(session, "卢克", scene)

        payload = client.get(f"/api/sessions/{sid}/state").json()
        by_name = {item["name"]: item for item in payload["unfiled"]}
        assert by_name["卢克"]["present"] is True
        assert by_name["卢克"]["location"] == scene
        # 没被安置过的人也在名单里——玩家离开酒馆后他还挂得住。
        assert by_name["米娅"]["present"] is False
        assert by_name["米娅"]["location"] == ""


def test_world_reset_clears_keys_but_not_cards(tmp_path) -> None:
    """键是运行态 → 随世界重置清零；卡是资产 → 工作台里的一张不少。"""
    with _api(tmp_path) as (client, sid, session):
        cards_before = sorted(session.world.npcs)
        session.ledger.save.unfiled.append("卢克")
        session.ledger.save.featured_counts["卢克"] = 2

        assert client.post(f"/api/sessions/{sid}/reset").status_code == 200

        session = client.app.state.sessions[sid]
        assert session.ledger.save.unfiled == []
        assert session.ledger.save.featured_counts == {}
        assert sorted(session.world.npcs) == cards_before  # 人物卡不受影响


# --------------------------------------------------------------------------
# 落卡 / 放弃落卡（写世界的路径只有一条）
# --------------------------------------------------------------------------

def test_evidence_is_mechanical_extraction(tmp_path) -> None:
    """提案只给"正文已经写出什么"——相关事件原文，不生成任何档案字段。"""
    with _api(tmp_path) as (client, sid, session):
        scene = session.ledger.current_scene()
        session.ledger.save.unfiled.append("卢克")
        _place(session, "卢克", scene)

        body = client.get(f"/api/sessions/{sid}/unfiled/卢克/evidence").json()
        assert body["name"] == "卢克"
        assert body["location"] == scene
        assert [e["location"] for e in body["events"]] == [scene]
        # 只给证据，不给结论：没有 appearance / persona 这类被"代笔"的字段。
        assert "appearance" not in body and "persona" not in body


def test_file_card_writes_asset_and_hands_key_back(tmp_path) -> None:
    """落卡 = 一张真正的人物卡（能进工作台、能被注入），键随之交还。"""
    with _api(tmp_path) as (client, sid, session):
        scene = session.ledger.current_scene()
        session.ledger.save.unfiled.append("卢克")
        session.ledger.save.featured_counts["卢克"] = 2
        _place(session, "卢克", scene)

        r = client.post(
            f"/api/sessions/{sid}/unfiled/卢克/file",
            json={"appearance": "灰袍，左手有旧疤", "persona": "话少", "has_actor": False},
        )
        assert r.status_code == 200, r.text

        # 卡真的落进了世界（资产文件 + 工作台可见）。
        assert "卢克" in session.world.npcs
        assert (session.world_dir / "npcs" / "卢克.json").is_file()
        assert "卢克" in client.get(f"/api/sessions/{sid}/world").json()["npcs"]
        # 键交还给卡：两处运行态都清干净，他不再需要计数。
        assert session.ledger.save.unfiled == []
        assert session.ledger.save.featured_counts == {}
        # 从"未落卡"变成"有卡"，在场资格无缝接上（不再走 unfiled 那条域）。
        assert "卢克" in session.ledger.present_at(scene)
        out = build_work_order("writer", session.world, session.ledger, scene)
        assert "[卢克]" in out and "在场临时角色" not in out


def test_file_rejects_name_not_in_unfiled(tmp_path) -> None:
    """没获键的人不能凭空落卡——入口必须来自未落卡名单。"""
    with _api(tmp_path) as (client, sid, _session):
        r = client.post(f"/api/sessions/{sid}/unfiled/张三/file", json={})
        assert r.status_code == 404


def test_discard_clears_count_so_he_can_be_improv_again(tmp_path, settings) -> None:
    """放弃落卡必须**同时清零计数**，否则下一轮立刻重新获键（假撤销）。"""
    with _api(tmp_path) as (client, sid, session):
        session.ledger.save.unfiled.append("卢克")
        session.ledger.save.featured_counts["卢克"] = 5

        assert client.post(f"/api/sessions/{sid}/unfiled/卢克/discard").status_code == 200
        assert session.ledger.save.unfiled == []
        assert "卢克" not in session.ledger.save.featured_counts
        # 名字不在名单里时 404——不会出现"撤销一个已落卡者"的怪操作。
        assert client.post(f"/api/sessions/{sid}/unfiled/卢克/discard").status_code == 404

        # 撤销之后重新出场：计数从 1 起算，不会立刻又获键。
        _adopt_two_rounds(session, settings, featured=["卢克"], participants=["刘星", "卢克"])
        assert session.ledger.save.unfiled == ["卢克"]  # 两轮之后才重新获键
        assert session.ledger.save.featured_counts["卢克"] == 2
