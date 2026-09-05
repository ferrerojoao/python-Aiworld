from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import WORLD_ROOT, GENERIC_LLM_RESPONSE


def _make_client(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
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


def test_director_backstage_override(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        r = client.post(
            f"/api/sessions/{sid}/director",
            json={
                "topic": "backstage",
                "action": "override",
                "payload": {"subject": "npc_zhuming", "location": "school_gate"},
            },
        )
        assert r.status_code == 200
        event = r.json()["event"]
        assert event["source"] == "director"


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
        assert len(trace) >= 3  # director, storyteller, qc
        assert trace[0]["messages"]


def test_world_save_as_when_has_save(tmp_path):
    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        world = client.get(f"/api/sessions/{sid}/world").json()

        # In-place save must be blocked because qinghsi already has a save.
        r = client.put(
            "/api/worlds/qinghsi",
            json={
                "overview": world["overview"],
                "lorebook": world["lorebook"],
                "scenes": world["scenes"],
                "npcs": world["npcs"],
                "axes": world["axes"],
            },
        )
        assert r.status_code == 409

        # Save-as should create a new world.
        r = client.post(
            "/api/worlds/qinghsi/save-as",
            json={
                "new_world_id": "qinghsi_edit",
                "overview": world["overview"],
                "lorebook": world["lorebook"],
                "scenes": world["scenes"],
                "npcs": world["npcs"],
                "axes": world["axes"],
            },
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
            json={"style": "轻快明亮", "description_style": "简短", "pace": "fast"},
        )
        assert put.status_code == 200
        state = client.get(f"/api/sessions/{sid}/state").json()
        assert state["preset"]["style"] == "轻快明亮"
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

        put = client.put(
            "/api/settings",
            json={
                "llm_base_url": "http://example.test/v1",
                "llm_api_key": "test-key",
                "model_main": "test-model",
                "model_cheap": "test-model",
            },
        )
        assert put.status_code == 200
        settings = client.get("/api/settings").json()
        assert settings["llm_base_url"] == "http://example.test/v1"


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