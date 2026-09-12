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
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        player = client.get(f"/api/sessions/{sid}/player").json()
        assert player["name"] == "你"

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

        player = client.get(f"/api/sessions/{sid}/player").json()
        assert player["name"] == "林晓"
        assert player["private_note"] == "其实是镇长的私生子"
        assert player["personal_secrets"] == "欠了赌债"


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

        # Design C: editing writes the save's world instance, never the asset.
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
        # The content-pack template itself is untouched.
        import json as _json

        from pathlib import Path as _Path

        template = _json.loads(_Path(tmp_path / "qinghsi" / "world.json").read_text(encoding="utf-8"))
        assert template["name"] == "青石镇"

        # Save-as promotes the instance (with evolution) to a new template.
        r = client.post(
            f"/api/sessions/{sid}/world/save-as",
            json={"new_world_id": "qinghsi_edit", **payload},
        )
        assert r.status_code == 200
        worlds = client.get("/api/worlds").json()["worlds"]
        assert any(w["id"] == "qinghsi_edit" for w in worlds)


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

        reset = client.post(f"/api/sessions/{sid}/reset")
        assert reset.status_code == 200
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        # After a reset only the world's opening event remains.
        assert len(events) == 1
        assert events[0]["source"] == "opening"

        # 重置不清世界实例：工作台编辑（NPC 卡 / 世界书）原样保留。
        world2 = client.get(f"/api/sessions/{sid}/world").json()
        assert world2["npcs"][npc_id]["persona"] == "重置后应保留的人设"
        assert any(e["id"] == "reset_probe" for e in world2["lorebook"])


def test_world_start_time_roundtrip(tmp_path):
    """world.json start_time seeds save clock, and reset returns to it."""
    import json as _json

    from pathlib import Path as _Path

    # The qinghsi fixture has start_time 2026-07-14T08:00:00.
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["clock"] == "2026-07-14T08:00:00"  # 内容包 start_time

        # 推钟后 reset 应回到起点（第二次输入自动采纳第一回合的候选）。
        client.post(f"/api/sessions/{sid}/turn", json={"input": "等到中午"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "然后呢"})
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["clock"] != "2026-07-14T08:00:00"
        client.post(f"/api/sessions/{sid}/reset")
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["clock"] == "2026-07-14T08:00:00"

        # 空 start_time 回退引擎默认：直接改内容包再建新存档。
        world_json = _Path(tmp_path / "qinghsi" / "world.json")
        data = _json.loads(world_json.read_text(encoding="utf-8"))
        data.pop("start_time", None)
        world_json.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
        r2 = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main2"})
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
        assert goal["subject"] == "player"
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

        # Goal cap is enforced (6 active).
        for i in range(6):
            client.post(
                f"/api/sessions/{sid}/director",
                json={"topic": "confirm", "action": {"type": "set_goal", "payload": {"text": f"目标{i}", "kind": "small"}}},
            )
        over = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": {"type": "set_goal", "payload": {"text": "超限目标", "kind": "small"}}},
        )
        assert over.status_code == 400

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
    """UI 改的推理等级/质检开关落盘 data/settings.json，重启（新 app 实例）后恢复。"""
    with _make_client(tmp_path) as client:
        put = client.put(
            "/api/settings",
            json={"reasoning_effort": "high", "qc_enabled": False},
        )
        assert put.status_code == 200
        state = client.get("/api/settings").json()
        assert state["reasoning_effort"] == "high"
        assert state["qc_enabled"] is False
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