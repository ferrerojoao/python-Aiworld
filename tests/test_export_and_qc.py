"""Tests for the two new features: save export (with event log) and QC skip."""
from __future__ import annotations

import io
import json
import zipfile

from app.core.llm import FakeLLM
from tests.conftest import GENERIC_LLM_RESPONSE


def test_export_save_includes_events(tmp_path):
    from tests.test_api import _make_client

    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"})  # 触发自动采纳

        resp = client.get(f"/api/sessions/{sid}/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert "save.json" in names
        assert "events.jsonl" in names
        assert any(n.startswith("world/") for n in names)  # 世界实例一并导出
        # 候选是临时态，不入备份
        assert not any(n.startswith("candidates/") for n in names)

        # 事件日志内容完整（开场 + 已采纳回合）
        events = [json.loads(line) for line in zf.read("events.jsonl").decode("utf-8").splitlines() if line.strip()]
        assert len(events) >= 2
        assert events[0]["source"] == "opening"

        # save.json 带运行态
        save = json.loads(zf.read("save.json"))
        assert save["meta"]["save_name"] == "main"


def test_import_save_roundtrip(tmp_path):
    """Export then import: the save lands under content/<world>/saves/ with its
    event log intact; a name clash gets renamed instead of overwriting."""
    import base64

    from tests.test_api import _make_client

    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        client.put(
            f"/api/sessions/{sid}/player",
            json={"name": "刘星", "persona": "18岁高中生", "private_note": "底牌"},
        )
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"})

        blob = client.get(f"/api/sessions/{sid}/export").content

        # 导入同一份存档：已存在同名 → 自动改名，不覆盖原存档。
        resp = client.post(
            "/api/saves/import",
            json={"filename": "save.zip", "content": base64.b64encode(blob).decode()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["world_id"] == "qinghsi"
        assert data["save_name"].startswith("main_")  # 改名后的新存档
        assert data["sid"] == f"qinghsi:{data['save_name']}"

        # 导入的存档可打开：主角资料与事件日志都在。
        player = client.get(f"/api/sessions/{data['sid']}/player").json()
        assert player["name"] == "刘星"
        assert player["private_note"] == "底牌"
        events = client.get(f"/api/sessions/{data['sid']}/ledger/events").json()["events"]
        assert len(events) >= 2
        assert events[0]["source"] == "opening"

        # 坏包被拒绝。
        bad = client.post(
            "/api/saves/import",
            json={"filename": "bad.zip", "content": base64.b64encode(b"not a zip").decode()},
        )
        assert bad.status_code == 400


def test_import_save_creates_missing_world(tmp_path):
    """导入的存档若指向本机不存在的世界，用包内 world/ 资产建档。"""
    import base64

    from tests.test_api import _make_client

    with _make_client(tmp_path) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        blob = client.get(f"/api/sessions/{sid}/export").content

        # 删掉世界目录，模拟换机（只有存档包）。
        import shutil as _shutil

        _shutil.rmtree(tmp_path / "qinghsi")
        assert not (tmp_path / "qinghsi").exists()

        resp = client.post(
            "/api/saves/import",
            json={"filename": "save.zip", "content": base64.b64encode(blob).decode()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["world_id"] == "qinghsi"
        assert (tmp_path / "qinghsi" / "world.json").exists()
        assert (tmp_path / "qinghsi" / "saves" / data["save_name"] / "events.jsonl").exists()
        # 世界资产从包内恢复（NPC 卡可读）
        world = client.get(f"/api/sessions/{data['sid']}/world").json()
        assert world["npcs"]


def test_qc_can_be_skipped(tmp_path):
    """qc_enabled=False → no QC call; writer prose goes straight to candidate."""
    from tests.test_api import _make_client

    llm = FakeLLM({"*": GENERIC_LLM_RESPONSE})
    with _make_client(tmp_path, llm=llm) as client:
        r = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})
        sid = r.json()["sid"]
        settings = client.app.state.settings
        assert settings.qc_enabled is True

        # 关闭质检：回合只调用编剧。
        settings.qc_enabled = False
        before = llm.usage["calls"]
        resp = client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        assert resp.status_code == 200
        assert llm.usage["calls"] - before == 1

        pending = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]
        assert pending and pending[0]["prose"] == GENERIC_LLM_RESPONSE["prose"]

        # 重新打开质检：回合 = 采纳上轮候选（审计 1 次）+ 编剧 + 质检。
        settings.qc_enabled = True
        before2 = llm.usage["calls"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "再问一句"})
        assert llm.usage["calls"] - before2 == 3  # audit(自动采纳) + writer + qc

        # API 契约：GET/PUT 往返（PUT 会重建 LLM 网关，故放在最后）。
        assert client.get("/api/settings").json()["qc_enabled"] is True
        put = client.put("/api/settings", json={"qc_enabled": False})
        assert put.status_code == 200
        assert client.get("/api/settings").json()["qc_enabled"] is False
