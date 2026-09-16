from __future__ import annotations

import json
import shutil

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import WORLD_ROOT, GENERIC_LLM_RESPONSE


def _make_client(tmp_path, llm=None):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    # data_dir must also be sandboxed: preset PUT would otherwise overwrite
    # the real data/presets.json with test values.
    settings = Settings(
        content_root=tmp_path,
        data_dir=tmp_path / "data",
        candidate_ttl_days=7,
    )
    if llm is None:
        llm = FakeLLM({"*": GENERIC_LLM_RESPONSE})
    app = create_app(settings=settings, llm=llm)
    return TestClient(app)


def test_create_session_and_turn_sse(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        assert r.status_code == 200
        sid = r.json()["sid"]

        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "去网吧找朱明"})
        assert r.status_code == 200
        assert "event: candidate" in r.text

        pending = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]
        assert len(pending) == 1


def test_reroll_keeps_old_and_adopt_cleans(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明昨天的事"})
        first_id = _candidate_id_from_sse(r.text)

        turn_id = _turn_id_from_sse(r.text)
        r = client.post(
            f"/api/sessions/{sid}/turns/{turn_id}/reroll",
            json={"mode": "rephrase", "note": "温和一点"},
        )
        assert r.status_code == 200
        second_id = r.json()["candidate_id"]

        # 重抽的 LLM 调用也要进调试面板（此前 reroll 用裸 LLM，trace 不可见）。
        # 「玩家要求：<note>」是重抽独有的调用痕迹，首回合的 trace 里不会有。
        trace_after_reroll = client.get(f"/api/sessions/{sid}/debug/latest").json()["trace"]
        assert trace_after_reroll
        blob = json.dumps(trace_after_reroll, ensure_ascii=False)
        assert "玩家要求：温和一点" in blob

        pending = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]
        assert len(pending) == 2

        r = client.post(f"/api/sessions/{sid}/candidates/{second_id}/adopt")
        assert r.status_code == 200
        pending = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]
        assert pending == []
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert len(events) == 2  # opening + adopted turn
        assert first_id != second_id


def test_adopt_route_records_settlement_and_trace(tmp_path):
    """点「采纳」时那笔审计调用必须进调试面板，结算留痕要透出给前端。

    此前采纳路由用 turn_runner_factory（裸 LLM）→ 调试面板里查无此 call；
    时间结算结果也算完即丢 → 玩家看到时钟跳了几小时查不出原因。
    """
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明昨天的事"})
        candidate_id = _candidate_id_from_sse(r.text)

        # 审计只在采纳时跑，所以首回合的 trace 里不该有它。
        first_trace = client.get(f"/api/sessions/{sid}/debug/latest").json()["trace"]
        assert not any(e.get("label") == "审计" for e in first_trace)

        r = client.post(f"/api/sessions/{sid}/candidates/{candidate_id}/adopt")
        assert r.status_code == 200

        trace = client.get(f"/api/sessions/{sid}/debug/latest").json()["trace"]
        assert any(e.get("label") == "审计" for e in trace)

        settlement = client.get(f"/api/sessions/{sid}/state").json()["last_settlement"]
        assert settlement["clock_before"]
        assert settlement["source"] in {"clock_to", "audit", "rule", "none"}
        assert settlement["delta_applied"] >= 0


def test_next_input_auto_adopts_single_candidate(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"})

        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert len(events) == 2  # opening + adopted turn
        pending = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]
        assert len(pending) == 1  # only the new turn's candidate


def test_multiple_candidates_blocks_new_turn(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        turn_id = _turn_id_from_sse(r.text)
        client.post(
            f"/api/sessions/{sid}/turns/{turn_id}/reroll",
            json={"mode": "redirect", "note": "换走向"},
        )

        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"})
        assert "error" in r.text
        assert "多份候选" in r.text


def test_turn_body_can_adopt_current_candidate(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        r = client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明昨天的事"})
        first_id = _candidate_id_from_sse(r.text)
        turn_id = _turn_id_from_sse(r.text)
        r = client.post(
            f"/api/sessions/{sid}/turns/{turn_id}/reroll",
            json={"mode": "rephrase", "note": "温和一点"},
        )
        second_id = r.json()["candidate_id"]

        r = client.post(
            f"/api/sessions/{sid}/turn",
            json={"input": "继续聊", "adopt_candidate_id": second_id},
        )
        assert "event: candidate" in r.text

        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert len(events) == 2  # opening + adopted turn
        pending = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]
        assert len(pending) == 1  # new turn's candidate


def test_director_confirm_override(tmp_path):
    """Silent override executes only after the player confirms the action."""
    from app.core.llm import FakeLLM

    class PrefixKeyLLM(FakeLLM):
        def _key(self, messages):
            for msg in reversed(messages):
                if msg.get("role") in {"user", "system"}:
                    content = msg.get("content", "")
                    for prefix in self.responses:
                        if content.startswith(prefix):
                            return prefix
            return "*"

    llm = PrefixKeyLLM(
        {
            "朱明在网吧": {
                "reply": "好的，我将记录朱明此刻在网吧。",
                "action": {"type": "override", "payload": {"subject": "朱明", "location": "网吧"}},
            }
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "朱明在网吧"},
        )
        assert chat.status_code == 200
        pending = chat.json()["pending_action"]
        assert pending["type"] == "override"

        # Before confirm: no director event in the ledger.
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert all(e["source"] != "director" for e in events)

        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": pending},
        )
        assert confirm.status_code == 200
        event = confirm.json()["event"]
        assert event["source"] == "director"
        assert event["location"] == "网吧"


def test_director_retire_marks_lifecycle(tmp_path):
    """导演窗口 retire：确认后 entities.lifecycle = retired（present_at 跳过），
    并落一条导演留痕事件；退场者从此不在任何在场名单里。"""
    from app.core.llm import FakeLLM

    class PrefixKeyLLM(FakeLLM):
        def _key(self, messages):
            for msg in reversed(messages):
                if msg.get("role") in {"user", "system"}:
                    content = msg.get("content", "")
                    for prefix in self.responses:
                        if content.startswith(prefix):
                            return prefix
            return "*"

    llm = PrefixKeyLLM(
        {
            "让朱明永久退场": {
                "reply": "好的，朱明将永久退场。",
                "action": {"type": "retire", "payload": {"npc_id": "朱明"}},
            }
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        session = client.app.state.sessions[sid]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "让朱明永久退场"},
        )
        assert chat.status_code == 200
        pending = chat.json()["pending_action"]
        assert pending["type"] == "retire"

        # 确认前未标记。
        assert session.ledger.save.entities.get("朱明") is None
        assert all(e["source"] != "director" for e in session.ledger.events)

        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": pending},
        )
        assert confirm.status_code == 200
        event = confirm.json()["event"]
        assert event["source"] == "director"
        assert "退场" in event["summary"]

        # 标记生效 + present_at 跳过（无论快照在哪都不再出现在任何场景）。
        entity = session.ledger.save.entities["朱明"]
        assert entity.lifecycle == "retired"
        for scene in session.world.scenes:
            assert "朱明" not in session.ledger.present_at(scene.id)


def test_player_profile_update(tmp_path):
    """主角就是人物表里的 is_player 卡：读回来的名字即卡键，改名走改名路径。"""
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        player = client.get(f"/api/sessions/{sid}/player").json()
        assert player["name"] == "刘星"
        assert player["is_player"] is True
        # 主角也是世界人物表里的一员（前端"人物"页能看到主角）。
        npcs = client.get(f"/api/sessions/{sid}/world").json()["npcs"]
        assert npcs["刘星"]["is_player"] is True

        put = client.put(
            f"/api/sessions/{sid}/player",
            json={
                "name": "林晓",
                "appearance": "黑发",
                "persona": "冷静",
                "private_note": "其实是镇长的私生子",
                "personal_secrets": "欠了赌债",
            },
        )
        assert put.status_code == 200
        assert put.json()["name"] == "林晓"

        player = client.get(f"/api/sessions/{sid}/player").json()
        assert player["name"] == "林晓"
        assert player["private_note"] == "其实是镇长的私生子"
        assert player["personal_secrets"] == "欠了赌债"
        # 改名 = 换人物表键：旧键消失，新键带着主角标记留下。
        npcs = client.get(f"/api/sessions/{sid}/world").json()["npcs"]
        assert "刘星" not in npcs
        assert npcs["林晓"]["is_player"] is True


def test_opening_event_anchors_player_at_start_scene(tmp_path):
    """开场就是主角的第一条位置事实——否则第一轮工作单的在场名单里没有主角。"""
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert events[0]["location"] == "主街"
        assert events[0]["participants"] == ["刘星"]
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["scene_id"] == "主街"
        # 状态栏的「在场」是"还有谁在"——主角不列进自己的视野
        assert state["present"] == []
        assert state["scene"].startswith("这里是主街。")


def test_player_cannot_be_retired(tmp_path):
    """主角不可退场：他是人物表里的一员，退场等于把主角从世界注销。"""
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        act = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": {"type": "retire", "payload": {"npc_id": "刘星"}}},
        )
        assert act.status_code == 400
        assert "主角" in act.json()["detail"]

        # 普通 NPC 照旧可退场
        act = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": {"type": "retire", "payload": {"npc_id": "朱明"}}},
        )
        assert act.status_code == 200


def test_world_edit_requires_exactly_one_player(tmp_path):
    """主角不可删掉（服务端兜底）：保存世界资产时没有主角卡 → 400。"""
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        world = client.get(f"/api/sessions/{sid}/world").json()

        payload = {
            "overview": world["overview"],
            "lorebook": world["lorebook"],
            "scenes": world["scenes"],
            "npcs": {"朱明": world["npcs"]["朱明"]},  # 把主角卡删掉
            "axes": world["axes"],
        }
        put = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert put.status_code == 400
        assert "主角" in put.json()["detail"]


def test_debug_trace_available_after_turn(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明昨天的事"})
        trace = client.get(f"/api/sessions/{sid}/debug/latest").json()["trace"]
        assert len(trace) >= 2  # writer, qc (merged agent pipeline)
        assert trace[0]["messages"]


def test_world_edit_and_save_as(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        world = client.get(f"/api/sessions/{sid}/world").json()

        # 单一真相源：编辑写的就是 content/<world>/ 本身，即时生效。
        payload = {
            "overview": world["overview"],
            "lorebook": world["lorebook"],
            "scenes": world["scenes"],
            "npcs": world["npcs"],
            "axes": world["axes"],
        }
        payload["overview"]["name"] = "青石镇（已改）"
        r = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert r.status_code == 200
        world2 = client.get(f"/api/sessions/{sid}/world").json()
        assert world2["overview"]["name"] == "青石镇（已改）"
        import json as _json

        from pathlib import Path as _Path

        on_disk = _json.loads(_Path(tmp_path / "qinghsi" / "world.json").read_text(encoding="utf-8"))
        assert on_disk["name"] == "青石镇（已改）"

        # Save-as 复制当前世界（含演化）为新内容包——fork，不动原世界。
        r = client.post(
            f"/api/sessions/{sid}/world/save-as",
            json={"new_world_id": "qinghsi_edit", **payload},
        )
        assert r.status_code == 200
        worlds = client.get("/api/worlds").json()["worlds"]
        assert any(w["id"] == "qinghsi_edit" for w in worlds)
        assert any(w["id"] == "qinghsi" and w["name"] == "青石镇（已改）" for w in worlds)


def test_delete_world_removes_save(tmp_path):
    with _make_client(tmp_path) as client:
        client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        r = client.delete("/api/worlds/qinghsi")
        assert r.status_code == 200
        worlds = client.get("/api/worlds").json()["worlds"]
        assert worlds == []


def test_global_preset_is_separate_from_world(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        put = client.put(
            "/api/presets",
            json={"writer_guidelines": "轻快明亮", "banned_words": ["烟头", "好面子"]},
        )
        assert put.status_code == 200
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["preset"]["writer_guidelines"] == "轻快明亮"
        assert state["preset"]["banned_words"] == ["烟头", "好面子"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        assert "presets" not in world


def test_world_export_and_import(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        export = client.get(f"/api/sessions/{sid}/world/export")
        assert export.status_code == 200
        assert export.content[:2] == b"PK"

        import base64

        r = client.post(
            f"/api/sessions/{sid}/world/import",
            json={
                "filename": "qinghsi.zip",
                "content": base64.b64encode(export.content).decode(),
            },
        )
        assert r.status_code == 200
        assert r.json()["world_id"].startswith("qinghsi")


def test_world_browser_and_reset(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"})

        world = client.get(f"/api/sessions/{sid}/world").json()
        assert world["overview"]["id"] == "qinghsi"
        assert len(world["scenes"]) > 0
        assert len(world["npcs"]) > 0
        assert len(world["events"]) >= 1

        # 工作台编辑实例（改一张 NPC 卡 + 一条世界书），随后重置应保留。
        npc_id = next(iter(world["npcs"]))
        world["npcs"][npc_id]["persona"] = "重置后应保留的人设"
        world["lorebook"] = list(world.get("lorebook") or []) + [
            {"id": "reset_probe", "keywords": ["重置探针"], "body": "reset lore probe"}
        ]
        put = client.put(f"/api/sessions/{sid}/world", json=world)
        assert put.status_code == 200

        # 上面的 turn 采纳过候选 → 此刻应有结算留痕（否则下面那条断言会空过）。
        assert client.get(f"/api/sessions/{sid}/state").json()["last_settlement"]

        reset = client.post(f"/api/sessions/{sid}/reset")
        assert reset.status_code == 200
        # 结算留痕（诊断字段）必须随重置清空：留着的话顶栏时钟 hover 显示的
        # 是重置前那一次的结算（时钟/依据档位全对不上，2026-09-14）。
        assert client.get(f"/api/sessions/{sid}/state").json()["last_settlement"] is None
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        # After a reset only the world's opening event remains.
        assert len(events) == 1
        assert events[0]["source"] == "opening"

        # 重置不清世界实例：工作台编辑（NPC 卡 / 世界书）原样保留。
        world2 = client.get(f"/api/sessions/{sid}/world").json()
        assert world2["npcs"][npc_id]["persona"] == "重置后应保留的人设"
        assert any(e["id"] == "reset_probe" for e in world2["lorebook"])


def test_state_exposes_states_and_change_trace(tmp_path):
    """/state 透出角色状态与最近一次变更留痕；重置即清零（Step 2a，2026-09-14）。

    后端是 A 档唯一的交付面（前端展现 / 撤销入口之后再做），所以这里的口径必须
    钉死：`id` 一定要露（撤销靠它精确定位）、`public` 一定要露（展现时区分表现）、
    `last_state_change` 一定要露（长期事实误加的代价高，得查得出原因）。
    """
    from app.ledger.save import EntityRuntime, StateItem

    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["states"] == {}
        assert state["last_state_change"] is None

        session = client.app.state.sessions[sid]
        player = session.world.player_name()
        session.ledger.save.entities[player] = EntityRuntime(
            states=[
                StateItem(
                    text="普通刀剑伤不了他",
                    since="2001-07-10T08:00:00",
                    source_event="ev_00001",
                    public=False,
                )
            ]
        )
        session.ledger.save.last_state_change = {
            "turn_id": "turn_probe",
            "at": "2001-07-10T08:00:00",
            "added": [],
            "removed": [],
            "expired": [],
            "skipped": [],
        }
        session.ledger.persist_save()

        state = client.get(f"/api/sessions/{sid}/state").json()
        item = state["states"][player][0]
        assert item["text"] == "普通刀剑伤不了他"
        assert item["id"].startswith("st_")  # 撤销靠这个 id，不能省
        assert item["public"] is False
        assert item["source_event"] == "ev_00001"  # 可追溯到"哪条正文给的"
        assert state["last_state_change"]["turn_id"] == "turn_probe"

        client.post(f"/api/sessions/{sid}/reset")
        state = client.get(f"/api/sessions/{sid}/state").json()
        # 重置世界即清零：这个世界重玩一遍，龙血也该重新喝。
        assert state["states"] == {}
        assert state["last_state_change"] is None


def test_revoke_state_route(tmp_path):
    """撤销接口（Step 2b）：删条目 + 落对冲记账事件；重复撤 → 404。"""
    from app.ledger.save import EntityRuntime, StateItem

    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        session = client.app.state.sessions[sid]
        player = session.world.player_name()

        # 空存档：没有任何状态 → 面板为空（2026-09-16：无状态的人不显示，含主角）。
        view = client.get(f"/api/sessions/{sid}/state").json()["state_view"]
        assert view == []

        session.ledger.save.entities[player] = EntityRuntime(
            states=[StateItem(text="普通刀剑伤不了他", public=False)]
        )
        session.ledger.persist_save()

        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["player_name"] == player  # 面板要单列主角
        entry = state["state_view"][0]
        assert [it["text"] for it in entry["visible"]] == ["普通刀剑伤不了他"]
        item_id = entry["visible"][0]["id"]

        assert (
            client.post(f"/api/sessions/{sid}/states/{item_id}/revoke").status_code == 200
        )
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["states"] == {}
        assert state["state_view"] == []  # 撤光了 → 这一组也不再显示
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert any(
            e.get("source") == "state" and "（玩家撤销）" in (e.get("summary") or "")
            for e in events
        )
        # 已撤销的条目再撤一次 → 404，不做静默成功。
        assert (
            client.post(f"/api/sessions/{sid}/states/{item_id}/revoke").status_code == 404
        )


def test_world_start_time_roundtrip(tmp_path):
    """world.json start_time seeds save clock, and reset returns to it."""
    import json as _json

    from pathlib import Path as _Path

    # The qinghsi fixture has start_time 2026-07-14T08:00:00.
    # 让审计给出 4h 时长来推钟（规则侧的跳时解析已于 2026-09-16 下线，
    # 时钟现在只由审计推进——"等到中午"不再自动落 12:00）。
    resp = dict(GENERIC_LLM_RESPONSE, delta_minutes=240)
    with _make_client(tmp_path, llm=FakeLLM({"*": resp})) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["clock"] == "2026-07-14T08:00:00"  # 内容包 start_time

        # 推钟后 reset 应回到起点（第二次输入自动采纳第一回合的候选）。
        client.post(f"/api/sessions/{sid}/turn", json={"input": "在街上耗了一上午"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "然后呢"})
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["clock"] == "2026-07-14T12:00:00"
        client.post(f"/api/sessions/{sid}/reset")
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["clock"] == "2026-07-14T08:00:00"

        # 空 start_time 回退引擎默认：复制一个去掉 start_time 的新世界再开档
        # （世界=存档 1:1，同一世界不能开第二档）。
        import shutil as _shutil

        dst = _Path(tmp_path / "qinghsi_nost")
        _shutil.copytree(
            _Path(tmp_path / "qinghsi"),
            dst,
            ignore=_shutil.ignore_patterns("candidates", "save.json", "events.jsonl"),
        )
        world_json = dst / "world.json"
        data = _json.loads(world_json.read_text(encoding="utf-8"))
        data.pop("start_time", None)
        data["id"] = "qinghsi_nost"
        world_json.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
        r2 = client.post("/api/sessions", json={"world_id": "qinghsi_nost"})
        sid2 = r2.json()["sid"]
        state2 = client.get(f"/api/sessions/{sid2}/state").json()
        assert state2["clock"] == "2026-07-14T08:00:00"


def test_director_chat_pending_action_confirm(tmp_path):
    """Director chat may return a pending backstage action; it must NOT take
    effect until the player confirms (two-stage gate)."""
    from app.core.llm import FakeLLM

    class PrefixKeyLLM(FakeLLM):
        def _key(self, messages):
            for msg in reversed(messages):
                if msg.get("role") in {"user", "system"}:
                    content = msg.get("content", "")
                    for prefix in self.responses:
                        if content.startswith(prefix):
                            return prefix
            return "*"

    llm = PrefixKeyLLM(
        {
            "设立一个主线目标": {
                "reply": "好的，我来设立这个目标。",
                "action": {"type": "set_goal", "payload": {"text": "查明朱明打架的真相", "kind": "big", "npc_id": "朱明"}},
            }
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        # The chat proposes the action but does not apply it yet.
        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "设立一个主线目标"},
        ).json()
        assert chat["pending_action"]["type"] == "set_goal"

        # Nothing applied before confirmation: state has no goals yet.
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["goals"] == []

        # Confirm executes it.
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        goal = confirm.json()["goal"]
        assert goal["status"] == "active"
        assert goal["kind"] == "big"
        assert goal["subject"] == ""  # 空串哨兵 = 主角的目标（2026-09-13 改口径）
        assert goal["npc_id"] == "朱明"

        state = client.get(f"/api/sessions/{sid}/state").json()
        assert [g["text"] for g in state["goals"]] == ["查明朱明打架的真相"]
        assert state["goals"][0]["subject_name"] == "玩家"


def test_director_set_goal_npc_subject(tmp_path):
    """A player may set an NPC's goal via the director window (subject=npc_id);
    state resolves the display name."""
    llm = FakeLLM(
        {
            "*": {
                "reply": "好，王蓉的目标记下了。",
                "action": {
                    "type": "set_goal",
                    "payload": {"text": "让主角答应下周跟她一起去接货", "kind": "small", "subject": "王蓉", "npc_id": "王蓉"},
                },
            }
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "帮王蓉立个目标"},
        ).json()
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        goal = confirm.json()["goal"]
        assert goal["subject"] == "王蓉"

        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["goals"][0]["subject_name"] == "王蓉"


def test_director_goal_parent_link_and_state_tree(tmp_path):
    """父子层级（2026-09-12）：小目标可挂到某个大目标下；非法挂靠被拒；
    /state 把挂在活动大目标下的子目标（含已完成）一并给出，供侧栏显示 x/y；
    导演工作单吐目标 id（否则废弃/挂父在戏外窗口填不出来）。"""
    from app.core.llm import FakeLLM
    from app.core.workorder import build_director_chat_system
    from app.ledger.goals import complete_goals

    llm = FakeLLM({"*": {"reply": "好。", "action": None}})
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        session = client.app.state.sessions[sid]

        def confirm(payload):
            return client.post(
                f"/api/sessions/{sid}/director",
                json={"topic": "confirm", "action": {"type": "set_goal", "payload": payload}},
            )

        big = confirm({"text": "查明朱明打架的真相", "kind": "big"}).json()["goal"]
        kid = confirm(
            {"text": "帮朱明包扎伤口", "kind": "small", "big_goal_id": big["id"]}
        ).json()["goal"]
        assert kid["big_goal_id"] == big["id"]

        # 挂到不存在的大目标 → 拒绝（错误信息可读）。
        bad = confirm({"text": "挂空", "kind": "small", "big_goal_id": "goal_nope"})
        assert bad.status_code == 400
        assert "不存在" in bad.text

        # 导演视图必须带 id（戏外窗口靠它废弃/挂父）——此时子目标仍 active。
        director = build_director_chat_system(session.world, session.ledger, "网吧")
        assert f"[{big['id']}] [大目标/主线·玩家]" in director
        assert f"[{kid['id']}] [小目标/支线·玩家]" in director

        # 完成子目标后它仍进 /state（侧栏靠它算 x/y），状态为 done。
        complete_goals(session.ledger, [kid["id"]])
        state = client.get(f"/api/sessions/{sid}/state").json()
        by_id = {g["id"]: g for g in state["goals"]}
        assert by_id[kid["id"]]["status"] == "done"
        assert by_id[big["id"]]["kind"] == "big"
        # 目标树只列 active 子目标；完成度体现在大目标行的进度里。
        director2 = build_director_chat_system(session.world, session.ledger, "网吧")
        assert "（子目标 1/1 已完成）" in director2

        # 父闭合 → 其下子目标级联撤下，且不再出现在 /state 的活动树里。
        kid2 = confirm(
            {"text": "问清打架缘由", "kind": "small", "big_goal_id": big["id"]}
        ).json()["goal"]
        confirm({"goal_id": big["id"], "status": "abandoned"})
        state2 = client.get(f"/api/sessions/{sid}/state").json()
        ids2 = {g["id"] for g in state2["goals"]}
        assert kid2["id"] not in ids2
        assert big["id"] not in ids2


def test_director_confirm_set_goal_and_inject_memory(tmp_path):
    """set_goal creates the goal; inject_memory lays a private
    event visible only to that NPC. Goal cap rejects overflow."""
    from app.core.llm import FakeLLM

    class PrefixKeyLLM(FakeLLM):
        def _key(self, messages):
            for msg in reversed(messages):
                if msg.get("role") in {"user", "system"}:
                    content = msg.get("content", "")
                    for prefix in self.responses:
                        if content.startswith(prefix):
                            return prefix
            return "*"

    llm = PrefixKeyLLM(
        {
            "设立支线目标帮王蓉修船": {
                "reply": "好的，记下这个支线目标。",
                "action": {
                    "type": "set_goal",
                    "payload": {"text": "帮王蓉修好渔船", "kind": "small", "npc_id": "王蓉"},
                },
            },
            "给朱明注入一段记忆": {
                "reply": "好的，我会私下记下这条。",
                "action": {
                    "type": "inject_memory",
                    "payload": {"npc_id": "朱明", "memory": "朱明心里记着：那天刘星在网吧替他挡了一下。"},
                },
            },
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "设立支线目标帮王蓉修船"},
        ).json()
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        goal = confirm.json()["goal"]
        assert goal["kind"] == "small"
        assert goal["status"] == "active"

        # 分档额度（2026-09-12）：未挂靠支线上限 2 条（上面已用 1 条），
        # 第 3 条被拒，错误信息指名是哪一层满了。
        client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": {"type": "set_goal", "payload": {"text": "孤儿一", "kind": "small"}}},
        )
        over = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": {"type": "set_goal", "payload": {"text": "孤儿溢出", "kind": "small"}}},
        )
        assert over.status_code == 400
        assert "未挂靠的支线已达上限" in over.text

        chat2 = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "给朱明注入一段记忆"},
        ).json()
        confirm2 = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat2["pending_action"]},
        )
        assert confirm2.status_code == 200
        ev = confirm2.json()["event"]
        assert ev["known_by"] == ["朱明"]
        assert ev["location"] is None

        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        injected = next(e for e in events if e["id"] == ev["id"])
        assert injected["known_by"] == ["朱明"]


def test_director_confirm_abandon_goal(tmp_path):
    """A player can abandon an active goal via the director window."""
    from app.core.llm import FakeLLM

    class PrefixKeyLLM(FakeLLM):
        def _key(self, messages):
            for msg in reversed(messages):
                if msg.get("role") in {"user", "system"}:
                    content = msg.get("content", "")
                    for prefix in self.responses:
                        if content.startswith(prefix):
                            return prefix
            return "*"

    llm = PrefixKeyLLM(
        {
            "不再追查打架的事了": {
                "reply": "好的，我废弃这个目标。",
                "action": {"type": "set_goal", "payload": {"text": "查明朱明打架的真相", "kind": "big"}},
            },
            "废弃那个目标": {
                "reply": "好，废弃。",
                "action": None,
            },
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "不再追查打架的事了"},
        ).json()
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        goal_id = confirm.json()["goal"]["id"]

        # Abandon it directly (the engineer-level path; chat may also propose).
        abandon = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": {"type": "set_goal", "payload": {"goal_id": goal_id, "status": "abandoned"}}},
        )
        assert abandon.status_code == 200
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["goals"] == []  # abandoned goals drop out of active


def test_settings_and_director_chat(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "下一步怎么发展？"},
        )
        assert chat.status_code == 200
        assert chat.json()["reply"]

        settings = client.get("/api/settings").json()
        assert "llm_base_url" in settings
        assert "reasoning_effort" in settings
        # 注入上限（2026-09-16）：三个数字都从系统设置透出。
        assert {"event_log_limit", "event_log_full", "known_set_limit"} <= set(settings)

        put = client.put(
            "/api/settings",
            json={
                "llm_base_url": "http://example.test/v1",
                "llm_api_key": "test-key",
                "model_main": "test-model",
                "model_cheap": "test-model",
                "reasoning_effort": "low",
            },
        )
        assert put.status_code == 200
        settings = client.get("/api/settings").json()
        assert settings["llm_base_url"] == "http://example.test/v1"
        assert settings["reasoning_effort"] == "low"


def test_settings_persist_across_restart(tmp_path):
    """UI 改的推理等级/质检开关/注入上限落盘 data/settings.json，重启后恢复。"""
    with _make_client(tmp_path) as client:
        put = client.put(
            "/api/settings",
            json={
                "reasoning_effort": "high",
                "qc_enabled": False,
                # 0 = 给全部（合法值，不该被夹成 1）
                "event_log_limit": 0,
                "event_log_full": 1,
                "known_set_limit": 0,
            },
        )
        assert put.status_code == 200
        state = client.get("/api/settings").json()
        assert state["reasoning_effort"] == "high"
        assert state["qc_enabled"] is False
        assert state["event_log_limit"] == 0
        assert state["event_log_full"] == 1
        assert state["known_set_limit"] == 0
        assert (tmp_path / "data" / "settings.json").exists()

    # 重启 = 全新 Settings（回到 env 默认）+ 同一 data_dir → lifespan 应用覆盖层。
    restarted = create_app(
        settings=Settings(
            content_root=tmp_path,
            data_dir=tmp_path / "data",
            candidate_ttl_days=7,
        ),
        llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}),
    )
    with TestClient(restarted) as client:
        state = client.get("/api/settings").json()
        assert state["reasoning_effort"] == "high"
        assert state["qc_enabled"] is False
        assert state["event_log_limit"] == 0
        assert state["event_log_full"] == 1
        assert state["known_set_limit"] == 0


# ---------------------------------------------------------------------------
# 快速造世界：L1 种子 / L2 一句话草稿（2026-09-13）
# ---------------------------------------------------------------------------


def test_create_blank_world_is_immediately_playable(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post(
            "/api/worlds/new",
            json={
                "world_id": "blank1",
                "name": "空白镇",
                "player_name": "刘星",
                "start_scene": "主街",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "world_id": "blank1", "problems": []}

        data = json.loads((tmp_path / "blank1" / "world.json").read_text(encoding="utf-8"))
        assert data["name"] == "空白镇"
        assert data["start_scene"] == "主街"
        assert (tmp_path / "blank1" / "npcs" / "刘星.json").exists()

        # 列表读 world.json 的 name（此前一律显示目录名），并带校验结论
        entry = next(
            w for w in client.get("/api/worlds").json()["worlds"] if w["id"] == "blank1"
        )
        assert entry["name"] == "空白镇"
        assert entry["ok"] is True and entry["problems"] == []

        # 校验接口（check_world 此前只挂在 loader CLI 上）
        assert client.get("/api/worlds/blank1/check").json() == {
            "ok": True,
            "problems": [],
        }

        # 几分钟开一局：新世界立刻能建档开局，开场事件把主角锚在开局场景
        sid = client.post(
            "/api/sessions", json={"world_id": "blank1", "save_name": "main"}
        ).json()["sid"]
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert len(events) == 1
        assert events[0]["location"] == "主街"
        assert events[0]["participants"] == ["刘星"]


def test_create_world_rejects_bad_id_missing_player_and_duplicate(tmp_path):
    with _make_client(tmp_path) as client:
        bad_id = client.post(
            "/api/worlds/new", json={"world_id": "坏 id", "player_name": "刘星"}
        )
        assert bad_id.status_code == 400
        assert not (tmp_path / "坏 id").exists()

        no_player = client.post("/api/worlds/new", json={"world_id": "ok1"})
        assert no_player.status_code == 400
        assert not (tmp_path / "ok1").exists()

        assert (
            client.post(
                "/api/worlds/new", json={"world_id": "ok1", "player_name": "刘星"}
            ).status_code
            == 200
        )
        dup = client.post(
            "/api/worlds/new", json={"world_id": "ok1", "player_name": "刘星"}
        )
        assert dup.status_code == 409


def test_create_world_from_draft_payload(tmp_path):
    payload = {
        "overview": {
            "name": "草稿镇",
            "summary": ["九十年代的县城"],
            "opening": "你站在主街上。",
            "start_time": "",
            "start_scene": "主街",
        },
        "lorebook": [],
        "scenes": [{"id": "主街", "aliases": [], "perceivable": "", "region": ""}],
        "npcs": {
            "刘星": {"id": "刘星", "is_player": True},
            "朱明": {"id": "朱明", "has_actor": True},
        },
        "axes": [],
    }
    with _make_client(tmp_path) as client:
        r = client.post(
            "/api/worlds/new", json={"world_id": "draft1", "payload": payload}
        )
        assert r.status_code == 200, r.text
        assert r.json()["problems"] == []
        data = json.loads((tmp_path / "draft1" / "world.json").read_text(encoding="utf-8"))
        assert data["name"] == "草稿镇"
        assert set(
            p.stem for p in (tmp_path / "draft1" / "npcs").glob("*.json")
        ) == {"刘星", "朱明"}


def test_create_world_rejects_unclean_assets_before_writing(tmp_path):
    payload = {
        "overview": {"name": "坏包", "start_scene": "不存在的地方"},
        "lorebook": [],
        "scenes": [{"id": "主街"}],
        "npcs": {"刘星": {"id": "刘星", "is_player": True}},
        "axes": [],
    }
    with _make_client(tmp_path) as client:
        r = client.post("/api/worlds/new", json={"world_id": "bad1", "payload": payload})
        assert r.status_code == 400
        assert "start_scene" in r.json()["detail"]
        assert not (tmp_path / "bad1").exists()  # 坏包根本不进 content/


def test_draft_endpoint_previews_without_writing(tmp_path):
    llm = FakeLLM(
        {
            "*": {
                "name": "草稿镇",
                "player_name": "刘星",
                "start_scene": "主街",
                "scenes": [{"id": "主街"}, {"id": "朱明家"}],
                "npcs": [{"id": "朱明", "persona": "同桌", "has_actor": True}],
                "lorebook": [{"id": "镇子", "keywords": ["青石镇"], "body": "县城"}],
            }
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post(
            "/api/worlds/draft",
            json={"premise": "县城高中暑假", "world_id": "draft2", "player_name": "刘星"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["problems"] == []
        assert body["assets"]["overview"]["name"] == "草稿镇"
        assert set(body["assets"]["npcs"]) == {"刘星", "朱明"}
        assert not (tmp_path / "draft2").exists()  # 预览不落盘

        assert client.post("/api/worlds/draft", json={"premise": "  "}).status_code == 400


def test_world_edit_soft_warns_and_hard_blocks(tmp_path):
    """就地编辑的两级校验（2026-09-13）：语义问题随响应带回（软警告），
    不变量（主角卡）先于落盘硬拦——坏数据存进去引擎就崩的，一个字节都不写。"""
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        payload = {
            "overview": world["overview"],
            "lorebook": world["lorebook"],
            "scenes": world["scenes"],
            "npcs": world["npcs"],
            "axes": world["axes"],
        }

        # 软警告：start_scene 指向不存在的场景 → 存进去能跑，但必须被看见
        payload["overview"]["start_scene"] = "不存在的地方"
        put = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert put.status_code == 200
        assert any("start_scene" in p for p in put.json()["problems"])

        # 硬拦：删掉主角卡 → 400，且原文件不动
        bad = dict(payload)
        bad["npcs"] = {"朱明": world["npcs"]["朱明"]}
        blocked = client.put(f"/api/sessions/{sid}/world", json=bad)
        assert blocked.status_code == 400
        assert "主角" in blocked.json()["detail"]
        assert client.get(f"/api/sessions/{sid}/world").json()["npcs"].get("刘星")


def test_world_check_targets_the_save_instance_not_the_template(tmp_path):
    """单一真相源：工作台 PUT 写的就是 content/<world>/ 本身，所以工作台
    体检与世界列表体检看到同一个文件——待修状态即时反映到世界列表。"""
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        payload = {
            "overview": world["overview"],
            "lorebook": world["lorebook"],
            "scenes": world["scenes"],
            "npcs": world["npcs"],
            "axes": world["axes"],
        }
        payload["overview"]["start_scene"] = "不存在的地方"
        assert client.put(f"/api/sessions/{sid}/world", json=payload).status_code == 200

        via_session = client.get(f"/api/sessions/{sid}/world/check").json()
        assert via_session["ok"] is False
        assert any("start_scene" in p for p in via_session["problems"])

        # 同一份文件：世界列表的体检结论同步变红。
        via_list = client.get("/api/worlds/qinghsi/check").json()
        assert via_list == via_session
        listed = client.get("/api/worlds").json()["worlds"]
        assert any(w["id"] == "qinghsi" and w["ok"] is False for w in listed)


def _candidate_id_from_sse(text: str) -> str:
    for block in text.split("\n\n"):
        if "event: candidate" in block and "candidate_id" in block:
            for line in block.splitlines():
                if line.startswith("data:"):
                    import json

                    return json.loads(line[5:].strip())["candidate_id"]
    raise AssertionError("candidate event not found")


def _turn_id_from_sse(text: str) -> str:
    for block in text.split("\n\n"):
        if "event: candidate" in block and "turn_id" in block:
            for line in block.splitlines():
                if line.startswith("data:"):
                    import json

                    return json.loads(line[5:].strip())["turn_id"]
    raise AssertionError("candidate event not found")


def _confirm(client, sid, action):
    return client.post(f"/api/sessions/{sid}/director", json={"topic": "confirm", "action": action})


def test_director_state_add_and_revoke(tmp_path):
    """导演窗口手动增/撤状态（Step 2c，2026-09-14）。

    玩家原本只有"审计提出 → 我撤销"这条单向链：审计漏报时他毫无补救手段。
    这两个动作补上另一半——"我要它存在"和"我要它不存在"都由玩家拍板。
    撤销刻意复用同一个 revoke_state，所以留痕、"（玩家撤销）"文案与抽屉里的 ×
    完全一致（同一个动作，不该有两套痕迹）。
    """
    with _make_client(tmp_path) as client:
        sid = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()["sid"]

        add = _confirm(
            client,
            sid,
            {
                "type": "state_add",
                "payload": {"npc_id": "朱明", "text": "左腿摔伤，走路瘸", "public": True},
            },
        )
        assert add.status_code == 200
        item = add.json()["state"]
        assert item["text"] == "左腿摔伤，走路瘸"
        assert item["public"] is True and item["expired_at"] == ""

        # 面板立刻看得到（带 id → 抽屉里就能撤）。
        state = client.get(f"/api/sessions/{sid}/state").json()
        entry = next(v for v in state["state_view"] if v["name"] == "朱明")
        assert [it["id"] for it in entry["visible"]] == [item["id"]]

        # 守卫只管格式与归属：空文本 400 / 不在人物表 404 / 重复 400。
        for payload, code in [
            ({"npc_id": "朱明", "text": "   "}, 400),
            ({"npc_id": "查无此人", "text": "腿伤"}, 404),
            ({"npc_id": "朱明", "text": "左腿摔伤，走路瘸"}, 400),
        ]:
            bad = _confirm(client, sid, {"type": "state_add", "payload": payload})
            assert bad.status_code == code, payload

        rev = _confirm(
            client, sid, {"type": "state_revoke", "payload": {"state_id": item["id"]}}
        )
        assert rev.status_code == 200 and rev.json()["state_id"] == item["id"]

        # 再撤一次 → 404：不做静默成功。
        assert (
            _confirm(
                client, sid, {"type": "state_revoke", "payload": {"state_id": item["id"]}}
            ).status_code
            == 404
        )

        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        assert any(
            e["summary"] == "朱明获得状态：左腿摔伤，走路瘸（玩家设定）" for e in events
        )
        assert any(
            e["summary"] == "朱明状态撤销：左腿摔伤，走路瘸（玩家撤销）" for e in events
        )


def test_director_state_revoke_falls_back_to_npc_and_text(tmp_path):
    """抄错 id 的退路：{npc_id, text} 做**精确文本**匹配。

    不做模糊匹配——"骨裂"与"左臂骨裂"必须分得开，猜错就是撤掉另一条事实。
    """
    with _make_client(tmp_path) as client:
        sid = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()["sid"]
        _confirm(
            client, sid, {"type": "state_add", "payload": {"npc_id": "朱明", "text": "左臂骨裂"}}
        )

        # 文本不完全一致 → 404（不猜）。
        miss = _confirm(
            client,
            sid,
            {"type": "state_revoke", "payload": {"npc_id": "朱明", "text": "骨裂"}},
        )
        assert miss.status_code == 404

        hit = _confirm(
            client,
            sid,
            {"type": "state_revoke", "payload": {"npc_id": "朱明", "text": "左臂骨裂"}},
        )
        assert hit.status_code == 200

        # 两个字段都不给 → 400。
        bare = _confirm(client, sid, {"type": "state_revoke", "payload": {}})
        assert bare.status_code == 400


def _adopt_round(client, sid, text="继续待着"):
    """跑一轮并采纳。采纳会写 last_state_change（修订留痕挂在它上面），
    并跑一次到期清算（已到期的条目靠它才被标记）。"""
    sse = client.post(f"/api/sessions/{sid}/turn", json={"input": text}).text
    client.post(f"/api/sessions/{sid}/candidates/{_candidate_id_from_sse(sse)}/adopt")


def test_director_state_revise_edits_in_place(tmp_path):
    """导演窗口修订状态（2026-09-16）：**原地改，不换条目**。

    "撤销 + 新增"也能换文字，但会把 since 重置成现在、把 source_event 指向新事件
    ——"他什么时候起就是这样的"这条信息会因为改一个错别字而丢掉。修订保留
    id / since / source_event 三个锚，改完仍是同一条事实（要反悔仍是同一个撤销入口）。

    只认 state_id、**不做 {npc_id, text} 退路**：那个退路在修订里天然有歧义
    （text 到底是"要改的那条"还是"改成什么"），猜错就是改错一条事实。
    """
    with _make_client(tmp_path) as client:
        sid = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()["sid"]
        _adopt_round(client, sid)

        add = _confirm(
            client,
            sid,
            {
                "type": "state_add",
                "payload": {"npc_id": "朱明", "text": "左腿摔伤，走路瘸", "public": True},
            },
        )
        item = add.json()["state"]

        rev = _confirm(
            client,
            sid,
            {"type": "state_revise", "payload": {"state_id": item["id"], "text": "右腿摔伤，走路瘸"}},
        )
        assert rev.status_code == 200
        after = rev.json()["state"]
        assert after["text"] == "右腿摔伤，走路瘸"
        # 三个锚全保留
        assert after["id"] == item["id"]
        assert after["since"] == item["since"]
        assert after["source_event"] == item["source_event"]
        assert after["public"] is True  # 没给的字段不动

        # 面板立刻是新文字（state_view 由引擎从存档现算）
        state = client.get(f"/api/sessions/{sid}/state").json()
        entry = next(v for v in state["state_view"] if v["name"] == "朱明")
        assert [it["text"] for it in entry["visible"]] == ["右腿摔伤，走路瘸"]

        # 留痕：revised 记新文字与旧文字（前端据此显示「旧 → 新」）；added 里仍是
        # 当年的快照——所以前端拿 revised 覆盖它，否则屏幕上会报旧字。
        change = state["last_state_change"]
        row = change["revised"][-1]
        assert row["id"] == item["id"]
        assert row["old_text"] == "左腿摔伤，走路瘸"
        assert row["text"] == "右腿摔伤，走路瘸"
        assert [a["text"] for a in change["added"] if a["id"] == item["id"]] == [
            "左腿摔伤，走路瘸"
        ]
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        summary = next(e["summary"] for e in events if "（玩家修订）" in (e["summary"] or ""))
        assert "左腿摔伤，走路瘸" in summary and "右腿摔伤，走路瘸" in summary

        # 只改期限 / 可见性：until 给空串＝清掉期限；public 显式给才动。
        lim = _confirm(
            client,
            sid,
            {
                "type": "state_revise",
                "payload": {
                    "state_id": item["id"],
                    "until": "2001-07-20T08:00:00",
                    "public": False,
                },
            },
        )
        assert lim.status_code == 200
        assert lim.json()["state"]["until"] == "2001-07-20T08:00:00"
        assert lim.json()["state"]["public"] is False
        clr = _confirm(
            client, sid, {"type": "state_revise", "payload": {"state_id": item["id"], "until": ""}}
        )
        assert clr.json()["state"]["until"] == ""

        # 提交与现值完全相同的字段 → 不落事件（没变化就不该留下"修订过"的痕迹）。
        before = len(client.get(f"/api/sessions/{sid}/ledger/events").json()["events"])
        same = _confirm(
            client,
            sid,
            {
                "type": "state_revise",
                "payload": {"state_id": item["id"], "text": "右腿摔伤，走路瘸", "until": ""},
            },
        )
        assert same.status_code == 200
        assert len(client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]) == before

        # 守卫：三项都不给 400 / 抄错 id 404 / 撞另一条已有文字 400 / 空文字 400。
        _confirm(
            client, sid, {"type": "state_add", "payload": {"npc_id": "朱明", "text": "左臂骨裂"}}
        )
        for payload, code in [
            ({"state_id": item["id"]}, 400),
            ({"state_id": "st_nope", "text": "随便"}, 404),
            ({"state_id": item["id"], "text": "左臂骨裂"}, 400),
            ({"state_id": item["id"], "text": "   "}, 400),
            ({}, 400),
        ]:
            assert _confirm(client, sid, {"type": "state_revise", "payload": payload}).status_code == code, payload

        # 已到期的条目不可修订：它在注入侧读不到，改文字玩家看不到效果；改期限更是
        # 要么立刻又失效、要么等于偷偷复活一条已经记过「状态结束」的史实。
        gone = _confirm(
            client,
            sid,
            {
                "type": "state_add",
                "payload": {"npc_id": "朱明", "text": "伤口未愈", "until": "2001-07-11T08:00:00"},
            },
        ).json()["state"]
        _adopt_round(client, sid)  # 采纳时到期清算 → expired_at 落上
        assert (
            _confirm(
                client,
                sid,
                {"type": "state_revise", "payload": {"state_id": gone["id"], "text": "伤口已愈"}},
            ).status_code
            == 400
        )

        # 撤销仍然有效，撤的是同一条（id 未变）。
        assert (
            _confirm(
                client, sid, {"type": "state_revoke", "payload": {"state_id": item["id"]}}
            ).status_code
            == 200
        )