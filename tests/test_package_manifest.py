"""导出包与存档的自描述（2026-10-03）。

诉求（用户原话）：「别的 AI 工具读取了一个生成的文件，立刻就能读到自我声明
这是一个什么东西」。三层声明各验一条：单文件头（world.json / save.json）、
包内机读清单、人读说明 —— 缺一层就有一种读者读不到。
"""
from __future__ import annotations

import base64
import io
import json
import shutil
import zipfile

from app.core.package import MANIFEST_NAME, README_NAME
from app.world.draft import blank_world_assets
from app.world.loader import load_world, save_world_assets
from tests.test_api import _make_client


def _start(client, world_id="qinghsi", save_name="main") -> str:
    r = client.post("/api/sessions", json={"world_id": world_id, "save_name": save_name})
    assert r.status_code == 200, r.text
    return r.json()["sid"]


def _open_package(blob: bytes) -> tuple[zipfile.ZipFile, dict]:
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return zf, json.loads(zf.read(MANIFEST_NAME))


# --- 单文件级 ---------------------------------------------------------------
def test_world_json_declares_itself_and_reads_back(tmp_path):
    """world.json 自带 `_format` 等四个头，且 ``load_world`` 读得回来（不丢）。

    这里顺带钉住一个地基：``_format`` 这种**下划线开头的字段名**在 pydantic v2 里
    被当私有属性**静默吞掉**（不进 model_fields、不进 model_dump，且不报错）
    ⇒ 真字段名不带下划线、靠 alias 保住盘上的外观。
    """
    root = tmp_path / "w1"
    save_world_assets(root, blank_world_assets("w1", "测试世界", "张三"))

    raw = json.loads((root / "world.json").read_text(encoding="utf-8"))
    assert raw["_format"] == "aiworld.world"
    assert raw["_format_version"] == 1
    assert raw["_generator"] == "AIWorld"
    assert "AIWorld" in raw["_about"]

    meta = load_world(root).meta
    assert meta.package_format == "aiworld.world"
    assert meta.package_format_version == 1


def test_save_json_declares_itself_on_disk(tmp_path):
    """save.json 盘上必须有 `_format`，且**不得**变成字段名 ``package_format``。

    save.json 是 ``model_dump()`` 出来的（不像 world.json 走显式 dict），所以这条
    同时在守 ``by_alias=True``：漏了它盘上会写成 ``package_format``，声明等于没写。
    两次落盘（建档 ``create_session`` 与每次结算 ``Ledger.persist_save``）各验一次。
    """
    with _make_client(tmp_path) as client:
        sid = _start(client)
        save_path = tmp_path / "qinghsi" / "save.json"
        meta = json.loads(save_path.read_text(encoding="utf-8"))["meta"]
        assert meta["_format"] == "aiworld.save"
        assert meta["_format_version"] == 1
        assert "package_format" not in meta

        # 第二回合触发自动采纳 ⇒ 走 Ledger.persist_save 那条落盘路。
        assert client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"}).status_code == 200
        assert client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"}).status_code == 200
        meta = json.loads(save_path.read_text(encoding="utf-8"))["meta"]
        assert meta["_format"] == "aiworld.save"
        assert "package_format" not in meta


# --- 包级：机读清单 + 人读说明 ----------------------------------------------
def test_export_world_package_has_manifest_and_readme(tmp_path):
    """资产包：清单排在包最前、人读说明在、**条目不手写**、且不含进度。"""
    with _make_client(tmp_path) as client:
        sid = _start(client)
        blob = client.get(f"/api/sessions/{sid}/world/export").content

    zf, m = _open_package(blob)
    names = zf.namelist()
    assert names[0] == MANIFEST_NAME  # 浏览/解压第一眼就是"这是什么"
    assert README_NAME in names

    assert m["_format"] == "aiworld.package"
    assert m["_format_version"] == 1
    assert m["kind"] == "world_assets"
    assert m["kind_label"]
    assert m["created_at"]
    assert m["world"]["id"] == "qinghsi"

    paths = [e["path"] for e in m["entries"]]
    assert "world.json" in paths
    assert MANIFEST_NAME not in paths  # 清单不列自己
    assert README_NAME not in paths

    # 清单**由实际写进包的路径聚合而来**：条目的顶层名必须与包内容一致
    # （手写清单会在这里漂移，比如加了 assets/ 却忘了写）。
    assert {p.split("/")[0] for p in paths} == {
        n.split("/")[0] for n in names if n not in (MANIFEST_NAME, README_NAME)
    }

    # 资产包不含进度。
    assert "save.json" not in names
    assert "events.jsonl" not in names
    assert not any(n.startswith("candidates/") for n in names)

    # 人读说明也是渲染出来的（读它的人不需要会看 JSON）。
    readme = zf.read(README_NAME).decode("utf-8-sig")
    assert "这是什么" in readme
    assert "怎么打开" in readme
    assert "qinghsi" in readme


def test_export_save_package_has_manifest(tmp_path):
    """完整备份：种类正确，且清单里带着存档标识（时钟 / 事件条数）。"""
    with _make_client(tmp_path) as client:
        sid = _start(client)
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
        client.post(f"/api/sessions/{sid}/turn", json={"input": "继续问"})
        blob = client.get(f"/api/sessions/{sid}/export").content

    _zf, m = _open_package(blob)
    assert m["kind"] == "full_backup"
    assert m["save"]["name"] == "main"
    assert m["save"]["clock"]
    assert m["save"]["events"] >= 1
    paths = [e["path"] for e in m["entries"]]
    assert "save.json" in paths
    assert "events.jsonl" in paths


def test_import_does_not_swallow_package_metadata(tmp_path):
    """导入不吃包级说明：世界目录里不该出现清单 / 说明。

    它们是「那一个 zip 的」说明，落进世界目录后会跟着下一次导出被打包，
    于是新包里多出第二份同名说明，正文还是旧的。
    """
    with _make_client(tmp_path) as client:
        sid = _start(client)
        blob = client.get(f"/api/sessions/{sid}/export").content
        shutil.rmtree(tmp_path / "qinghsi")
        resp = client.post(
            "/api/saves/import",
            json={"filename": "s.zip", "content": base64.b64encode(blob).decode()},
        )
        assert resp.status_code == 200, resp.text

    root = tmp_path / "qinghsi"
    assert (root / "save.json").exists()  # 世界内容照常恢复
    assert (root / "world.json").exists()
    assert not (root / MANIFEST_NAME).exists()
    assert not (root / README_NAME).exists()
