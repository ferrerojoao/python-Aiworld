from __future__ import annotations

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
                "action": {"type": "override", "payload": {"subject": "npc_zhuming", "location": "net_bar"}},
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
        assert event["location"] == "net_bar"


def test_player_profile_update(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        player = client.get(f"/api/sessions/{sid}/player").json()
        assert player["name"] == "你"

        put = client.put(
            f"/api/sessions/{sid}/player",
            json={"name": "林晓", "appearance": "黑发", "persona": "冷静", "background": "转学生"},
        )
        assert put.status_code == 200

        player = client.get(f"/api/sessions/{sid}/player").json()
        assert player["name"] == "林晓"
        assert player["background"] == "转学生"


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

        reset = client.post(f"/api/sessions/{sid}/reset")
        assert reset.status_code == 200
        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        # After a reset only the world's opening event remains.
        assert len(events) == 1
        assert events[0]["source"] == "opening"


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
            "让王蓉配 Agent": {
                "reply": "好的，王蓉将配 Actor。",
                "action": {"type": "set_actor", "payload": {"npc_id": "npc_wangrong", "has_actor": True}},
            }
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        # The chat proposes the action but does not apply it yet.
        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "让王蓉配 Agent"},
        ).json()
        assert chat["pending_action"]["type"] == "set_actor"

        # Nothing applied before confirmation: instance card still has_actor=False.
        import json

        card = json.loads(
            (tmp_path / "qinghsi" / "saves" / "main" / "world" / "npcs" / "npc_wangrong.json").read_text(encoding="utf-8")
        )
        assert card["has_actor"] is False

        # Confirm executes it on the instance card.
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        assert confirm.json()["has_actor"] is True

        card = json.loads(
            (tmp_path / "qinghsi" / "saves" / "main" / "world" / "npcs" / "npc_wangrong.json").read_text(encoding="utf-8")
        )
        assert card["has_actor"] is True


def test_director_confirm_set_actor_and_inject_memory(tmp_path):
    """set_actor writes the runtime has_actor; inject_memory lays a private
    event visible only to that NPC."""
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
            "给王蓉配 Agent": {
                "reply": "好的，王蓉将配给 Actor。",
                "action": {"type": "set_actor", "payload": {"npc_id": "npc_wangrong", "has_actor": True}},
            },
            "给朱明注入一段记忆": {
                "reply": "好的，我会私下记下这条。",
                "action": {
                    "type": "inject_memory",
                    "payload": {"npc_id": "npc_zhuming", "memory": "朱明心里记着：那天刘星在网吧替他挡了一下。"},
                },
            },
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "给王蓉配 Agent"},
        ).json()
        assert chat["pending_action"]["type"] == "set_actor"
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        assert confirm.json()["has_actor"] is True

        # Promotion survives reload and lives on the world-instance card.
        import json

        card = json.loads(
            (tmp_path / "qinghsi" / "saves" / "main" / "world" / "npcs" / "npc_wangrong.json").read_text(encoding="utf-8")
        )
        assert card["has_actor"] is True

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
        assert ev["known_by"] == ["npc_zhuming"]
        assert ev["location"] is None

        events = client.get(f"/api/sessions/{sid}/ledger/events").json()["events"]
        injected = next(e for e in events if e["id"] == ev["id"])
        assert injected["known_by"] == ["npc_zhuming"]


def test_director_confirm_create_npc_and_add_scene(tmp_path):
    """Player-driven promotion: a grabbed character becomes an instance NPC
    card; a new place becomes a navigable instance scene."""
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
            "给那个老乞丐建档": {
                "reply": "好的，我来给老乞丐建档。",
                "action": {
                    "type": "create_npc",
                    "payload": {"name": "老乞丐", "appearance": "破棉袄，走路一瘸一拐。", "has_actor": False},
                },
            },
            "街角新开了家奶茶店": {
                "reply": "好的，我把它注册为新地点。",
                "action": {"type": "add_scene", "payload": {"name": "街角奶茶店", "perceivable": "门脸不大，招牌是手写的。"}},
            },
        }
    )
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]

        chat = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "给那个老乞丐建档"},
        ).json()
        confirm = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat["pending_action"]},
        )
        assert confirm.status_code == 200
        npc_id = confirm.json()["npc_id"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        assert npc_id in world["npcs"]
        assert world["npcs"][npc_id]["name"] == "老乞丐"

        chat2 = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "chat", "message": "街角新开了家奶茶店"},
        ).json()
        confirm2 = client.post(
            f"/api/sessions/{sid}/director",
            json={"topic": "confirm", "action": chat2["pending_action"]},
        )
        assert confirm2.status_code == 200
        scene_id = confirm2.json()["scene_id"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        assert any(s["id"] == scene_id and s["name"] == "街角奶茶店" for s in world["scenes"])


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