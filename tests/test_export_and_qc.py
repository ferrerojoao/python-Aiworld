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
