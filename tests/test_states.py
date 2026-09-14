"""角色状态 · 长期事实（REQ 〇章「角色状态」）的装配覆盖。

Step 1 = 数据 + 注入（编剧 / 审计两处），**不含自动产生**——所以这些测试都是
先手塞状态、再断装配结果。NPC Actor 的可见性过滤（``public``）属 Step 3，
这里反过来钉住"现在不给 Actor"：将来把状态接进 Actor 切片时本测试会失败，
提醒设计者必须同时实现 public 过滤，不要照搬全量。
"""

from __future__ import annotations

import json

from app.core.workorder import (
    build_actor_work_order,
    build_audit_work_order,
    build_director_chat_system,
    build_work_order,
    state_view,
)
from app.ledger.save import EntityRuntime, StateItem
from app.runtime.session import open_session


def _seed(session, name: str, *texts: str, public: bool = False, until: str = "") -> None:
    runtime = session.ledger.save.entities.setdefault(name, EntityRuntime())
    for text in texts:
        runtime.states.append(StateItem(text=text, public=public, until=until))


def _place(session, npc_id: str) -> str:
    """把某 NPC 放到主角当前场景：在场推导只认事件，没有别的入口。"""
    scene = session.ledger.current_scene()
    session.ledger.append(
        {
            "id": session.ledger.allocate_event_id(),
            "kind": "narrative",
            "at": session.ledger.save.clock,
            "location": scene,
            "participants": [npc_id],
            "known_by": None,
            "body": "",
            "summary": "",
        }
    )
    return scene


def _an_npc(session) -> str:
    player = session.world.player_name()
    return next(name for name in session.world.npcs if name != player)


def _writer_order(session) -> str:
    return build_work_order(
        "writer", session.world, session.ledger, session.ledger.current_scene()
    )


def test_player_state_line_rides_with_the_card(session) -> None:
    """主角状态紧贴人物卡行成对（都在「玩家资料」段内）。"""
    _seed(session, session.world.player_name(), "沐浴龙血，普通刀剑伤不了他")
    out = _writer_order(session)
    assert "[主角] 状态：沐浴龙血，普通刀剑伤不了他" in out
    assert out.index("[主角] 名字：") < out.index("[主角] 状态：")


def test_npc_state_line_injected_when_present(session) -> None:
    npc = _an_npc(session)
    _place(session, npc)
    _seed(session, npc, "怕水")
    assert f"[{npc}] 状态：怕水" in _writer_order(session)


def test_absent_npc_state_stays_out(session) -> None:
    """不在场者的状态不进编剧视野——在场名单是唯一入口。"""
    npc = _an_npc(session)
    _seed(session, npc, "怕水")
    assert f"[{npc}] 状态：" not in _writer_order(session)


def test_no_block_when_states_are_empty(session) -> None:
    assert "[主角] 状态：" not in _writer_order(session)


def test_blank_text_is_skipped(session) -> None:
    _seed(session, session.world.player_name(), "   ")
    assert "[主角] 状态：" not in _writer_order(session)


def test_player_cap_truncates_by_entry_order(session) -> None:
    """上限 6，按**录入顺序**截断（重要的往上放，不是按时间）。"""
    _seed(session, session.world.player_name(), *[f"状态{i}" for i in range(1, 9)])
    out = _writer_order(session)
    assert "状态1" in out and "状态6" in out
    assert "状态7" not in out


def test_npc_cap_is_three(session) -> None:
    npc = _an_npc(session)
    _place(session, npc)
    _seed(session, npc, *[f"n{i}" for i in range(1, 6)])
    out = _writer_order(session)
    assert "n1" in out and "n3" in out
    assert "n4" not in out


def test_until_renders_as_a_date(session) -> None:
    _seed(session, session.world.player_name(), "左臂骨裂", until="2001-07-20T08:00:00")
    assert "[主角] 状态：左臂骨裂（至 2001-07-20）" in _writer_order(session)


def test_audit_work_order_carries_states_with_ids(session) -> None:
    """审计必须知道状态（否则会判出与状态自相矛盾的结论），且要看得见 id。"""
    player = session.world.player_name()
    _seed(session, player, "沐浴龙血，普通刀剑伤不了他")
    out = build_audit_work_order(
        session.world, session.ledger, session.ledger.current_scene()
    )
    assert "角色状态（长期事实，正文必须与之自洽" in out
    item = session.ledger.save.entities[player].states[0]
    assert f"[{player}] [{item.id}] 沐浴龙血，普通刀剑伤不了他" in out


def test_writer_order_hides_state_ids(session) -> None:
    """id 只给审计——编剧不需要，也不该去引用它。"""
    player = session.world.player_name()
    _seed(session, player, "沐浴龙血")
    item = session.ledger.save.entities[player].states[0]
    out = _writer_order(session)
    assert "[主角] 状态：沐浴龙血" in out
    assert item.id not in out


def test_audit_has_no_states_block_when_empty(session) -> None:
    out = build_audit_work_order(
        session.world, session.ledger, session.ledger.current_scene()
    )
    assert "角色状态（长期事实" not in out


def test_truncated_states_are_announced(session) -> None:
    """被上限截断时必须显式说明，不能静默消失（否则玩家会想"我明明沐浴了龙血"）。"""
    _seed(session, session.world.player_name(), *[f"状态{i}" for i in range(1, 9)])
    out = _writer_order(session)
    assert "另有 2 条状态未列出" in out


def test_state_add_discipline_rejects_transient_facts(session) -> None:
    """门槛钉在这里：消息 / 情报、一次性当下、别人的隐秘都不算状态。

    用户反馈原话（2026-09-14）："这个状态不能太简单就能上了啊，什么她妈住院，
    这种简单的事没必要上状态的"。这段纪律是**主闸**——中文语义判据做不成机械
    守卫（"额角缠着纱布（前几天打架留的）"里就含事件信息，但它确实是状态），
    所以靠提示词，改动它之前先看这条测试。
    """
    out = build_audit_work_order(
        session.world, session.ledger, session.ledger.current_scene()
    )
    assert "过几天、几十轮回头看" in out  # 判据问句：够不够"长期"
    assert "消息、情报、听说的别人的事" in out  # 反例 ①：她妈住院 → 事件，不是状态
    assert "剧情目标" in out  # 并指路：要长期追踪的情节走目标
    assert "一次性的当下" in out  # 反例 ②：心情 / 手上沾泥 / 拿着棍子
    assert "人物卡" in out  # 反例 ③：那个角色自己的隐秘与前史
    assert "拿不准 → 空数组" in out  # 宁缺勿滥


def test_state_add_discipline_forbids_aims_and_hearsay(session) -> None:
    """只收"已成事实"：意图、别人的评价都不算（原文纪律不能退化）。"""
    out = build_audit_work_order(
        session.world, session.ledger, session.ledger.current_scene()
    )
    assert "不写意图、不写别人的评价" in out
    assert "宁缺勿滥" in out


def test_actor_view_does_not_leak_states(session) -> None:
    """哨兵：Step 1 有意不给 Actor（纯属未实现 public 过滤前的防泄漏护栏）。"""
    npc = _an_npc(session)
    scene = _place(session, npc)
    _seed(session, npc, "怕水", public=True)
    out = build_actor_work_order(session.world, session.ledger, npc, scene)
    assert "状态：" not in out


def test_old_save_without_states_still_opens(session) -> None:
    """老存档（entities 里只有 lifecycle）必须照常打开：pydantic 默认值兜住，无需迁移。"""
    raw = json.loads(session.ledger.save_path.read_text(encoding="utf-8"))
    raw["entities"] = {"朱明": {"lifecycle": "retired"}}  # 2026-09-14 之前的形态
    session.ledger.save_path.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    reopened = open_session(session.root)
    assert reopened.ledger.save.entities["朱明"].lifecycle == "retired"
    assert reopened.ledger.save.entities["朱明"].states == []


# ---------------------------------------------------------------------------
# 状态面板视图（Step 2b，2026-09-14）—— 由引擎侧生成，供前端照渲染
# ---------------------------------------------------------------------------

def _view(session) -> list[dict]:
    scene = session.ledger.current_scene()
    return state_view(session.ledger, session.ledger.present_at(scene))


def _director_order(session) -> str:
    """导演窗口的工作单（OOC）——状态操作清单就在这里。"""
    return build_director_chat_system(
        session.world, session.ledger, session.ledger.current_scene()
    )


def test_state_view_orders_player_then_present_then_absent(session) -> None:
    """分组与排序只有一处（后端）；前端照渲染，不再自己算。"""
    player = session.world.player_name()
    npc = _an_npc(session)
    other = next(n for n in session.world.npcs if n not in (player, npc))
    _seed(session, player, "沐浴龙血")
    _place(session, npc)
    _seed(session, npc, "怕水")
    _seed(session, other, "独臂")

    view = _view(session)
    by = {v["name"]: v for v in view}
    assert view[0]["name"] == player  # 主角恒在最前（他永远在场）
    assert by[player]["is_player"] is True and by[player]["present"] is True
    assert by[npc]["present"] is True
    # 不在场的也进面板（含不在场是本档的要求：要查得到"朱明身上还有没有旧伤"），
    # 但 present=False → 前端灰显 + 注明"编剧本轮看不到"。
    assert by[other]["present"] is False
    assert [v["name"] for v in view].index(npc) < [v["name"] for v in view].index(other)


def test_state_view_hands_over_overflow_items(session) -> None:
    """超出上限的条目要**连条目一起**给前端（灰显 + 提示清理），不只是给个条数。"""
    player = session.world.player_name()
    _seed(session, player, *[f"状态{i}" for i in range(1, 9)])

    entry = next(v for v in _view(session) if v["is_player"])
    assert [it["text"] for it in entry["visible"]] == [f"状态{i}" for i in range(1, 7)]
    assert [it["text"] for it in entry["hidden"]] == ["状态7", "状态8"]
    # 同源校验：面板的 visible 与提示词看到的 6 条字面一致、hidden 与"另有 N 条"对得上。
    out = _writer_order(session)
    assert "状态6" in out and "状态7" not in out
    assert "另有 2 条状态未列出" in out


def test_state_view_skips_absent_names_without_states(session) -> None:
    """不在场又没有任何状态的人不进面板（列出来只是噪音）；主角即使空也进。"""
    player = session.world.player_name()
    view = _view(session)
    assert [v["name"] for v in view] == [player]
    assert view[0]["visible"] == [] and view[0]["hidden"] == []


def test_state_view_does_not_fake_truncation_for_absent_names(session) -> None:
    """不在场者的条目不截断：cap 是**注入上限**，他压根不注入。

    否则面板会对不在场的人说"这几条超出注入上限，编剧看不到"——原因说错了
    （真实原因是他不在场），而且造出"前 3 条正常、后 2 条超限"的假分层。
    """
    npc = _an_npc(session)
    _seed(session, npc, *[f"n{i}" for i in range(1, 6)])  # 不在场
    entry = next(v for v in _view(session) if v["name"] == npc)
    assert entry["present"] is False
    assert [it["text"] for it in entry["visible"]] == [f"n{i}" for i in range(1, 6)]
    assert entry["hidden"] == []


def test_state_view_still_truncates_for_present_names(session) -> None:
    """在场者照旧按 cap 截断（这才是"超出注入上限"的真实含义）。"""
    npc = _an_npc(session)
    _place(session, npc)
    _seed(session, npc, *[f"n{i}" for i in range(1, 6)])
    entry = next(v for v in _view(session) if v["name"] == npc)
    assert [it["text"] for it in entry["visible"]] == ["n1", "n2", "n3"]
    assert [it["text"] for it in entry["hidden"]] == ["n4", "n5"]


def test_state_view_flags_retired(session) -> None:
    npc = _an_npc(session)
    _seed(session, npc, "已死之人")
    session.ledger.save.entities[npc].lifecycle = "retired"
    entry = next(v for v in _view(session) if v["name"] == npc)
    assert entry["retired"] is True
    assert entry["present"] is False


def test_state_view_carries_the_fields_needed_to_revoke(session) -> None:
    """面板要能就地撤销 → 每条必须带 id（前端靠它调撤销接口）。"""
    _seed(session, session.world.player_name(), "沐浴龙血")
    item = _view(session)[0]["visible"][0]
    assert item["id"].startswith("st_")
    assert set(item) >= {"id", "text", "since", "source_event", "public", "until"}


# ---------------------------------------------------------------------------
# 到期 = 失效不删除（2026-09-14 晚改）
# ---------------------------------------------------------------------------


def _expire(session, name: str, text: str) -> None:
    runtime = session.ledger.save.entities.setdefault(name, EntityRuntime())
    runtime.states.append(
        StateItem(text=text, until="2020-01-01T00:00:00", expired_at="2026-07-14T08:00:00")
    )


def test_expired_state_is_not_injected_to_writer_or_audit(session) -> None:
    """失效要真的停止影响正文：编剧与审计都读不到（否则会判出自相矛盾的结论）。"""
    npc = _an_npc(session)
    _place(session, npc)
    _expire(session, npc, "左腿摔伤")
    assert "左腿摔伤" not in _writer_order(session)
    audit_order = build_audit_work_order(
        session.world, session.ledger, session.ledger.current_scene()
    )
    assert "左腿摔伤" not in audit_order


def test_state_view_puts_expired_items_in_their_own_bucket(session) -> None:
    """已失效的另起一桶：不进 visible / hidden——那两桶都说"编剧看得到 / 超上限"，
    而这条的真实原因是"到期了"（面板要按这个原因灰显并给撤销入口）。"""
    npc = _an_npc(session)
    _place(session, npc)
    _seed(session, npc, "左腿摔伤")
    _expire(session, npc, "三天前的擦伤")
    entry = next(v for v in _view(session) if v["name"] == npc)
    assert [it["text"] for it in entry["visible"]] == ["左腿摔伤"]
    assert entry["hidden"] == []
    assert [it["text"] for it in entry["expired"]] == ["三天前的擦伤"]
    assert entry["expired"][0]["id"].startswith("st_")  # 带 id 才能撤


def test_state_view_keeps_an_absent_name_that_only_has_expired_items(session) -> None:
    """不在场 + 只剩失效条目：**也要进面板**——面板是唯一的撤销入口，
    漏掉这一组等于"这条永远清不掉"。"""
    npc = _an_npc(session)
    _expire(session, npc, "旧伤")
    entry = next(v for v in _view(session) if v["name"] == npc)
    assert entry["present"] is False
    assert entry["visible"] == [] and entry["hidden"] == []
    assert [it["text"] for it in entry["expired"]] == ["旧伤"]


def test_state_view_full_does_not_truncate(session) -> None:
    """full=True（导演侧）：不截断——导演要撤"被上限藏起来的那条"，看不见就无从下手。"""
    npc = _an_npc(session)
    _place(session, npc)
    _seed(session, npc, *[f"n{i}" for i in range(1, 6)])
    entry = next(
        v
        for v in state_view(
            session.ledger, session.ledger.present_at(session.ledger.current_scene()), full=True
        )
        if v["name"] == npc
    )
    assert [it["text"] for it in entry["visible"]] == [f"n{i}" for i in range(1, 6)]
    assert entry["hidden"] == []


def test_director_states_block_lists_everything_with_ids(session) -> None:
    """导演的操作清单：全量（含不在场 / 超上限 / 已到期）+ 逐条带 id。

    id 必须与面板**逐字一致**——玩家说"撤掉朱明那条腿伤"，导演要填的就是面板上
    那个 id；两处各算一遍迟早漂移。
    """
    npc = _an_npc(session)
    other = next(n for n in session.world.npcs if n not in {npc, session.world.player_name()})
    _place(session, npc)
    _seed(session, npc, *[f"n{i}" for i in range(1, 6)])  # 在场：第 4、5 条超上限
    _seed(session, other, "不在场的人的状态")
    _expire(session, other, "已到期的那条")
    out = _director_order(session)

    assert f"[{npc}]" in out
    assert all(f"] n{i}" in out for i in range(1, 6))  # 超上限的也在（一条一行带 id）
    assert "不在场的人的状态" in out
    assert f"[{other}]（不在场）" in out  # 组头标出原因
    assert "已到期）" in out  # 失效条目标了原因

    # id 与面板同源：清单里的 id 就是 state_view 给的 id。
    entry = next(v for v in _view(session) if v["name"] == npc)
    for it in entry["visible"]:
        assert f"[{it['id']}] {it['text']}" in out


def test_director_order_drops_the_duplicate_state_lines(session) -> None:
    """导演工单里状态只出现一处：名片行不再重复渲染（两份口径会比现场打架）。

    编剧工单不受影响，仍贴着人物卡成对渲染。
    """
    npc = _an_npc(session)
    _place(session, npc)
    _seed(session, npc, "怕水")
    out = _director_order(session)
    assert out.count("怕水") == 1
    assert "[怕水] 状态" not in out and f"[{npc}] 状态：怕水" not in out
    assert f"[{npc}] 状态：怕水" in _writer_order(session)
