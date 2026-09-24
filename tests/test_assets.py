"""附图（场景图 / 人物图）：上传口、取图口、归一化、孤儿清理（2026-09-24）。

规格与推导：``docs/方案-场景与人物附图-AIWorld.md`` §4.4 / §4.5。
代码：``app/world/images.py`` + ``app/api/routes_sessions.py`` 的 ``/assets`` 两个端点。

**每条用例都对应一个具体的坑**，不是凑覆盖率：

- 归一化：只缩不放（放大只会糊）、EXIF 旋转要摆正（手机的图会躺倒）
- 校验：扩展名/MIME 都能改 ⇒ 真判据是 Pillow 解码；"头对、身子烂"必须被拒
- 取图：路径穿越（直接读磁盘那条路）· **非本机无令牌必须 401**
- 字段：``PUT /world`` 是**全量重写**，图不回传 = 保存一次就抹掉
- 孤儿：上传只写文件、字段靠保存落盘 ⇒ 保存那一刻要清理没人引用的文件
- 导出：``assets/`` 必须随包（这是"复制为新世界 / 全量备份带上图"的前提）
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes_sessions
from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from app.world.images import (
    MAX_UPLOAD_BYTES,
    ImageRejected,
    normalize,
    resolve_asset,
    safe_subject,
)
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT

#: 明确的"网段内其它机器"地址（走非本机分支，但不是回环）。
REMOTE = ("192.168.31.50", 51234)
TOKEN = "s3cret-token"


# --------------------------------------------------------------------------
# 造图 / 起服务
# --------------------------------------------------------------------------


def png(w: int, h: int, color: tuple[int, int, int] = (200, 120, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def jpeg(w: int, h: int, orientation: int | None = None) -> bytes:
    img = Image.new("RGB", (w, h), (120, 60, 30))
    buf = io.BytesIO()
    if orientation is None:
        img.save(buf, format="JPEG")
    else:
        exif = Image.Exif()
        exif[274] = orientation  # 274 = Orientation
        img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def webp(w: int, h: int, color: tuple[int, int, int] = (40, 90, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="WEBP")
    return buf.getvalue()


def cmyk_jpeg(w: int, h: int) -> bytes:
    """印刷用 CMYK 图。JPEG 没有 alpha，但 **CMYK 直接存会抛**（`cannot write mode CMYK
    as JPEG`）——归一化必须先转 RGB。这条在电脑上随便传一张手机图是永远碰不到的。"""
    buf = io.BytesIO()
    Image.new("CMYK", (w, h)).save(buf, format="JPEG")
    return buf.getvalue()


def client_for(tmp_path: Path, *, addr=None, **overrides) -> TestClient:
    world_root = tmp_path / "qinghsi"
    if not world_root.exists():
        shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(
        content_root=tmp_path,
        data_dir=tmp_path / "data",
        candidate_ttl_days=7,
        **overrides,
    )
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    return TestClient(app, client=addr) if addr else TestClient(app)


def open_session(client: TestClient, headers: dict | None = None) -> str:
    """⚠️ 非本机客户端连**开会话**都要令牌，所以 headers 得能传进来（见取图 401 那条）。"""
    r = client.post(
        "/api/sessions",
        json={"world_id": "qinghsi", "save_name": "main"},
        headers=headers or {},
    )
    assert r.status_code == 200, r.text
    return r.json()["sid"]


def upload(client: TestClient, sid: str, kind: str, subject: str, data: bytes):
    return client.post(
        f"/api/sessions/{sid}/assets",
        params={"kind": kind, "subject": subject},
        content=data,
        headers={"Content-Type": "image/png"},
    )


def asset_folder(tmp_path: Path, kind: str = "scenes") -> Path:
    return tmp_path / "qinghsi" / "assets" / kind


# --------------------------------------------------------------------------
# 纯函数：命名 / 归一化（不经过 HTTP，把判定钉死）
# --------------------------------------------------------------------------


def test_safe_subject_strips_path_chars_and_dots():
    """主体名完全由客户端给，这里守的是**性质**（不是某个精确串）：
    出不了路径分隔符、开不了头也结不了尾的点、永不为空、有长度上限。"""
    traversal = safe_subject("../../etc/passwd", "scenes")
    assert "/" not in traversal and "\\" not in traversal
    assert not traversal.startswith(".") and not traversal.endswith(".")
    assert "etc" in traversal and "passwd" in traversal, "别把正常字吃掉了"

    assert safe_subject("鱼市", "scenes") == "鱼市"
    assert safe_subject("  ..鱼市..  ", "scenes") == "鱼市"
    assert safe_subject("..", "scenes") == "scene", "全是点 ⇒ 退化成空名 ⇒ 兜底"
    assert safe_subject("", "scenes") == "scene"
    assert safe_subject("   ", "npcs") == "npc"
    assert safe_subject("a" * 100, "scenes") == "a" * 40


def test_normalize_rejects_unknown_kind_and_empty():
    with pytest.raises(ImageRejected):
        normalize(png(800, 450), "trophies")
    with pytest.raises(ImageRejected):
        normalize(b"", "scenes")


def test_resolve_asset_refuses_to_escape_the_assets_folder(tmp_path):
    """取图端点**直接读磁盘**，所以路径那条路要单独钉死（越界一律 None）。"""
    folder = tmp_path / "assets" / "scenes"
    folder.mkdir(parents=True)
    (folder / "ok-1a2b3c4d.png").write_bytes(png(800, 450))
    (tmp_path / "world.json").write_text("{}", encoding="utf-8")

    assert resolve_asset(tmp_path, "scenes", "ok-1a2b3c4d.png") is not None
    for bad in ["../world.json", "..\\world.json", "/etc/passwd", "a.png/../../x.png", "..", "a.txt", ""]:
        assert resolve_asset(tmp_path, "scenes", bad) is None, f"{bad} 不该被解析成合法路径"
    assert resolve_asset(tmp_path, "trophies", "ok-1a2b3c4d.png") is None


def test_resolve_asset_second_layer_blocks_escape_on_its_own(tmp_path, monkeypatch):
    """两层防御要**各自**成立：第一层正则挡形状，第二层 ``resolve()`` 挡越界。

    ⚠️ 上面那条用得着的是**第一层** —— 带分隔符的名字在正则那关就没了，
    `path.parent != folder` 那句在真实输入下**摸不到**（变异测试证明：删掉它
    上面那条仍然全绿）。真要碰它只有"名字形状合法、解析后跑出目录"，
    现实里是**软链接 / 目录联接**；Windows 上建软链接要特权，所以这里直接把正则
    放宽，单独把第二层拎出来验。
    """
    import app.world.images as images

    folder = tmp_path / "assets" / "scenes"
    folder.mkdir(parents=True)
    (tmp_path / "world.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(images, "ASSET_NAME_RE", re.compile(r"^.*$"))

    # ⚠️ 退**两级**才跑得出 assets/（folder 是 assets/scenes）：`../world.json`
    #    只是退到 assets/，那里没这个文件，`is_file()` 自己就会返回 None ——
    #    那样测不到第二层，变异测试会绿（第一版就是这么写错的）。
    assert resolve_asset(tmp_path, "scenes", "../../world.json") is None, "第二层自己也得挡住"
    assert resolve_asset(tmp_path, "scenes", "ok.png") is None, "文件不存在照样得 None"


# --------------------------------------------------------------------------
# 归一化（走 HTTP，因为"落盘成什么样"是端到端的事实）
# --------------------------------------------------------------------------


def test_large_image_is_fitted_to_target_size(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", png(3000, 2000))
        assert r.status_code == 200, r.text
        saved = asset_folder(tmp_path) / Path(r.json()["path"]).name
        assert Image.open(saved).size == (1280, 720), "超过目标尺寸要缩到 1280×720（居中裁切）"


def test_small_enough_image_is_kept_as_is(tmp_path):
    """**只缩不放**：原图比目标小就不动它（插值放大只会糊，还骗人以为图很清晰）。

    ⚠️ 这里断言的是**字节不变**，不只是尺寸不变 —— 尺寸对了也可能是被重编码过一遍
    （JPEG 白掉一次画质、PNG 白烧一次 CPU）。`exif_transpose` 在没有旋转信息时
    **也会返回一份 copy**，曾让"原样返回"这条路整条失效，是靠覆盖率发现
    "`return raw` 那行从没被执行"才揪出来的。
    """
    with client_for(tmp_path) as client:
        sid = open_session(client)
        original = png(800, 450)
        r = upload(client, sid, "scenes", "主街", original)
        assert r.status_code == 200, r.text
        saved = asset_folder(tmp_path) / Path(r.json()["path"]).name
        assert saved.read_bytes() == original, "没动过的图必须原样落盘，不许重编码"
        assert Image.open(saved).size == (800, 450)


def test_exif_orientation_is_applied(tmp_path):
    """手机直出的照片带旋转信息：不摆正就是躺倒的，而这个 bug 在电脑上传的图上
    永远看不到。原图 1600×900 + orientation=6 ⇒ 摆正后应是 900×1600。"""
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", jpeg(1600, 900, orientation=6))
        assert r.status_code == 200, r.text
        saved = asset_folder(tmp_path) / Path(r.json()["path"]).name
        assert Image.open(saved).size == (900, 1600)


def test_exif_orientation_1_is_not_a_reason_to_reencode(tmp_path):
    """朝向标签 = 1（正常）等价于**没有标签**，不能因此重编码一遍。

    ⚠️ 这是"`exif_transpose` 没有旋转信息时也返回 copy"那个坑的另一半：
    `upright is not img` 会把"朝向正常"也判成改过 ⇒ 判据只能是标签值本身。
    """
    with client_for(tmp_path) as client:
        sid = open_session(client)
        original = jpeg(1200, 675, orientation=1)
        r = upload(client, sid, "scenes", "主街", original)
        assert r.status_code == 200, r.text
        saved = asset_folder(tmp_path) / Path(r.json()["path"]).name
        assert saved.read_bytes() == original, "朝向正常 = 没动过，必须原样落盘"
        assert Image.open(saved).size == (1200, 675)


def test_portrait_target_is_square(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "npcs", "朱明", png(2000, 1200))
        assert r.status_code == 200, r.text
        saved = asset_folder(tmp_path, "npcs") / Path(r.json()["path"]).name
        assert Image.open(saved).size == (512, 512)


# --------------------------------------------------------------------------
# 校验（四道闸）
# --------------------------------------------------------------------------


def test_garbage_is_rejected(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", b"<html>not an image</html>")
        assert r.status_code == 400
        assert "不是 PNG" in r.json()["detail"]


def test_png_header_with_broken_body_is_rejected(tmp_path):
    """🔴 只看 magic bytes 是拦不住这条的：头对、身子烂。

    ⚠️ 这里必须用**两个输入**，它们由不同的防线挡住 —— 这个区别是变异测试逼出来的：
    把 `verify()+load()` 整段拔掉后，只喂"头+垃圾"仍然全绿，因为
    `Image.open` 自己就会报 broken PNG，**根本走不到真解码那一步**；
    "真 PNG 砍尾巴"才是：IHDR 正常、结构能认，只有把像素真解一遍才会炸
    （砍掉那道闸后它不会退化成 400，而是在后面 `exif_transpose` 那里抛出去 ⇒ 500）。
    """
    with client_for(tmp_path) as client:
        sid = open_session(client)
        truncated = png(800, 450)[:-200]
        assert len(truncated) > 200, "砍尾要砍在 IDAT 里，别把整份图砍没"
        for label, bad in [
            ("PNG 头 + 垃圾", b"\x89PNG\r\n\x1a\n" + b"garbage" * 64),
            ("真 PNG 砍尾巴", truncated),
        ]:
            r = upload(client, sid, "scenes", "主街", bad)
            assert r.status_code == 400, f"{label} 应当 400，实际 {r.status_code}"
            detail = r.json()["detail"]
            assert "损坏" in detail or "无法解码" in detail, f"{label}：{detail}"
        assert not asset_folder(tmp_path).exists() or list(asset_folder(tmp_path).iterdir()) == []


def test_too_small_is_rejected_with_the_floor_in_the_message(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", png(300, 200))
        assert r.status_code == 400
        assert "太小" in r.json()["detail"]
        assert "640×360" in r.json()["detail"], "要告诉用户下限是多少，否则他只能猜"


def test_oversize_body_is_rejected_by_the_route_guard(tmp_path, monkeypatch):
    """路由侧按 Content-Length 预检（省掉整包读进内存）。"""
    monkeypatch.setattr(routes_sessions, "MAX_UPLOAD_BYTES", 10)
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", png(800, 450))
        assert r.status_code == 413


def test_oversize_body_is_rejected_by_the_normalizer(tmp_path, monkeypatch):
    """归一化侧按真实字节数再确认一次（Content-Length 可能缺失或撒谎）。"""
    import app.world.images as images

    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 10)
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", png(800, 450))
        assert r.status_code == 413


def test_unknown_kind_is_rejected(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "trophies", "奖杯", png(800, 450))
        assert r.status_code == 400
        assert "未知图片类型" in r.json()["detail"]


def test_webp_is_accepted_and_resized(tmp_path):
    """白名单里的第三种格式。⚠️ WebP 默认是**有损**的：重编码必须显式给质量，
    否则会拿默认值把用户的无损图悄悄压坏。"""
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", webp(3000, 2000))
        assert r.status_code == 200, r.text
        rel = r.json()["path"]
        assert rel.endswith(".webp"), "不改格式：进来是 WebP，出去还得是 WebP"
        assert Image.open(asset_folder(tmp_path) / Path(rel).name).size == (1280, 720)


def test_cmyk_jpeg_is_converted_before_saving(tmp_path):
    """CMYK（印刷）图不转 RGB 会在保存那一步抛 `cannot write mode CMYK as JPEG`。"""
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", cmyk_jpeg(3000, 2000))
        assert r.status_code == 200, r.text
        saved = Image.open(asset_folder(tmp_path) / Path(r.json()["path"]).name)
        assert saved.size == (1280, 720)
        assert saved.mode == "RGB"


def test_format_outside_the_whitelist_is_rejected(tmp_path, monkeypatch):
    """magic bytes 那三条只是**预筛**，真正的格式出口是 ``FORMAT_TO_EXT`` 白名单
    （预筛拦不住"能解码、但不是我们认的三种"的将来格式）。"""
    import app.world.images as images

    monkeypatch.delitem(images.FORMAT_TO_EXT, "PNG")
    with client_for(tmp_path) as client:
        sid = open_session(client)
        r = upload(client, sid, "scenes", "主街", png(800, 450))
        assert r.status_code == 400
        assert "不支持的图片格式" in r.json()["detail"]


def test_same_image_twice_reuses_one_file(tmp_path):
    """内容寻址命名：同名 = 同内容 ⇒ 传两次也只留一份。"""
    with client_for(tmp_path) as client:
        sid = open_session(client)
        first = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]
        second = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]
        assert first == second
        assert len(list(asset_folder(tmp_path).iterdir())) == 1


# --------------------------------------------------------------------------
# 取图口
# --------------------------------------------------------------------------


def test_fetch_returns_bytes_with_immutable_cache(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        rel = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]
        r = client.get(f"/api/sessions/{sid}/{rel}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert "immutable" in r.headers["cache-control"]
        assert Image.open(io.BytesIO(r.content)).size == (800, 450)


def test_fetch_missing_or_bad_kind_is_404(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        assert client.get(f"/api/sessions/{sid}/assets/scenes/nope.png").status_code == 404
        assert client.get(f"/api/sessions/{sid}/assets/trophies/a.png").status_code == 404
        assert client.get(f"/api/sessions/{sid}/assets/scenes/a.txt").status_code == 404


def test_fetch_requires_token_when_not_local(tmp_path):
    """🔴 这条是"<img src> 带不了 Authorization 头"那个坑的守卫。

    图片是世界数据（文件名还含人名 / 场景名），所以必须在 ``/api`` 下受令牌保护。
    ⚠️ 本机测试看不到这个问题 —— 只有**非本机**（手机 / 局域网）才复现，
    而那时前端若用 `<img src>` 直连就会 401，再被"缺图不显示"吞成静默消失。
    """
    overrides = {"auth_token": TOKEN, "require_auth_for_non_local": True}
    with client_for(tmp_path, addr=REMOTE, **overrides) as client:
        sid = open_session(client, {"Authorization": f"Bearer {TOKEN}"})
        rel = "assets/scenes/直接摆一张-1a2b3c4d.png"
        folder = asset_folder(tmp_path)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "直接摆一张-1a2b3c4d.png").write_bytes(png(800, 450))

        assert client.get(f"/api/sessions/{sid}/{rel}").status_code == 401
        ok = client.get(f"/api/sessions/{sid}/{rel}", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/png"

    # 回环地址照旧不要求令牌（本机单人使用的既有口径，别为新端点改掉）。
    # 用独立子目录：同一 save_name 再开一次会撞 409（"world already has a save"），
    # 那是"续玩"语义，不是本用例要验的东西。
    with client_for(tmp_path / "loopback", **overrides) as client:
        sid = open_session(client)
        rel = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]
        assert client.get(f"/api/sessions/{sid}/{rel}").status_code == 200


# --------------------------------------------------------------------------
# 字段往返 + 孤儿清理
# --------------------------------------------------------------------------


def test_scene_image_survives_world_round_trip(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        rel = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        scenes = [dict(s) for s in world["scenes"]]
        for scene in scenes:
            if scene["id"] == "主街":
                scene["image"] = rel
        r = client.put(
            f"/api/sessions/{sid}/world",
            json={
                "overview": world["overview"],
                "lorebook": world["lorebook"],
                "scenes": scenes,
                "npcs": world["npcs"],
            },
        )
        assert r.status_code == 200, r.text
        after = client.get(f"/api/sessions/{sid}/world").json()
        assert [s["image"] for s in after["scenes"] if s["id"] == "主街"] == [rel]


def test_npc_portrait_survives_world_round_trip(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        rel = upload(client, sid, "npcs", "朱明", png(600, 600)).json()["path"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        npcs = {k: dict(v) for k, v in world["npcs"].items()}
        assert "朱明" in npcs, "夹具世界里应该有朱明"
        npcs["朱明"]["portrait"] = rel
        r = client.put(
            f"/api/sessions/{sid}/world",
            json={
                "overview": world["overview"],
                "lorebook": world["lorebook"],
                "scenes": world["scenes"],
                "npcs": npcs,
            },
        )
        assert r.status_code == 200, r.text
        after = client.get(f"/api/sessions/{sid}/world").json()
        assert after["npcs"]["朱明"]["portrait"] == rel


def test_saving_world_prunes_unreferenced_assets(tmp_path):
    """上传只写文件、字段靠「保存」落盘 ⇒ 保存那一刻要把没人引用的图带走。

    这一条同时覆盖了「换图」与「移除」两种玩法：两者都只是把字段改成另一个值，
    旧文件从此没人引用。
    """
    with client_for(tmp_path) as client:
        sid = open_session(client)
        old = upload(client, sid, "scenes", "主街", png(800, 450, (1, 2, 3))).json()["path"]
        new = upload(client, sid, "scenes", "主街", png(800, 450, (4, 5, 6))).json()["path"]
        assert Path(old).name != Path(new).name
        assert len(list(asset_folder(tmp_path).iterdir())) == 2

        world = client.get(f"/api/sessions/{sid}/world").json()
        scenes = [dict(s) for s in world["scenes"]]
        for scene in scenes:
            if scene["id"] == "主街":
                scene["image"] = new  # 换图：old 变成孤儿
        body = {
            "overview": world["overview"],
            "lorebook": world["lorebook"],
            "scenes": scenes,
            "npcs": world["npcs"],
        }
        assert client.put(f"/api/sessions/{sid}/world", json=body).status_code == 200
        assert [p.name for p in asset_folder(tmp_path).iterdir()] == [Path(new).name]

        # 再走一遍「移除」：字段清空 ⇒ 连 new 也带走
        for scene in scenes:
            if scene["id"] == "主街":
                scene["image"] = ""
        assert client.put(f"/api/sessions/{sid}/world", json=body).status_code == 200
        assert list(asset_folder(tmp_path).iterdir()) == []


def test_pruning_a_world_without_any_images_is_a_no_op(tmp_path):
    """从没传过图的世界（**绝大多数存档的常态**）保存时也要安静通过。

    这是 `prune_orphan_assets` 里 `if not assets.is_dir(): return` 那个早退的守卫 ——
    早退写漏了就是每次保存都抛异常，而"从没传过图"恰恰是所有人第一次保存的样子。
    顺带钉住 `.tmp` 残骸会被扫掉（上传写到一半留下的）。
    """
    with client_for(tmp_path) as client:
        sid = open_session(client)
        world = client.get(f"/api/sessions/{sid}/world").json()
        body = {
            "overview": world["overview"],
            "lorebook": world["lorebook"],
            "scenes": world["scenes"],
            "npcs": world["npcs"],
        }
        assert client.put(f"/api/sessions/{sid}/world", json=body).status_code == 200
        assert not asset_folder(tmp_path).exists(), "没图就不该凭空建出 assets/"

        # 有 assets/ 但没有被任何字段引用（外加一个半截 .tmp）⇒ 全部带走
        folder = asset_folder(tmp_path)
        folder.mkdir(parents=True)
        (folder / "孤儿-00000000.png").write_bytes(png(800, 450))
        (folder / "半截-11111111.png.tmp").write_bytes(b"\x89PNG")
        assert client.put(f"/api/sessions/{sid}/world", json=body).status_code == 200
        assert list(folder.iterdir()) == []


def test_prune_keeps_assets_referenced_by_other_subjects(tmp_path):
    """清理只认"有没有被引用"，不能连带把别人还在用的图删掉。"""
    with client_for(tmp_path) as client:
        sid = open_session(client)
        scene_img = upload(client, sid, "scenes", "主街", png(800, 450, (1, 2, 3))).json()["path"]
        npc_img = upload(client, sid, "npcs", "朱明", png(600, 600, (4, 5, 6))).json()["path"]

        world = client.get(f"/api/sessions/{sid}/world").json()
        scenes = [dict(s) for s in world["scenes"]]
        for scene in scenes:
            if scene["id"] == "主街":
                scene["image"] = scene_img
        npcs = {k: dict(v) for k, v in world["npcs"].items()}
        npcs["朱明"]["portrait"] = npc_img
        r = client.put(
            f"/api/sessions/{sid}/world",
            json={
                "overview": world["overview"],
                "lorebook": world["lorebook"],
                "scenes": scenes,
                "npcs": npcs,
            },
        )
        assert r.status_code == 200, r.text
        assert [p.name for p in asset_folder(tmp_path).iterdir()] == [Path(scene_img).name]
        assert [p.name for p in asset_folder(tmp_path, "npcs").iterdir()] == [Path(npc_img).name]


# --------------------------------------------------------------------------
# 导出：assets/ 必须随包
# --------------------------------------------------------------------------


def test_export_bundles_the_assets_folder(tmp_path):
    """「复制为新世界 / 全量备份带上图」全靠两个导出端点走 rglob —— 这条钉住它。"""
    with client_for(tmp_path) as client:
        sid = open_session(client)
        rel = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]

        r = client.get(f"/api/sessions/{sid}/world/export")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
        assert rel in names, f"资产包里没有图：{names}"


# --------------------------------------------------------------------------
# /state 的附图字段
# --------------------------------------------------------------------------


def test_state_exposes_scene_image_and_present_view(tmp_path):
    with client_for(tmp_path) as client:
        sid = open_session(client)
        rel = upload(client, sid, "scenes", "主街", png(800, 450)).json()["path"]
        world = client.get(f"/api/sessions/{sid}/world").json()
        scenes = [dict(s) for s in world["scenes"]]
        for scene in scenes:
            if scene["id"] == "主街":
                scene["image"] = rel
        client.put(
            f"/api/sessions/{sid}/world",
            json={
                "overview": world["overview"],
                "lorebook": world["lorebook"],
                "scenes": scenes,
                "npcs": world["npcs"],
            },
        )

        data = client.get(f"/api/sessions/{sid}/state").json()
        # 开局事件落在 start_scene（主街），所以它此刻就是镜头场景
        assert data["scene_id"] == "主街"
        assert data["scene_image"] == rel
        assert "青石板" in data["scene_perceivable"], "给的是 perceivable 原文，不是拼好的 scene 描述"
        assert data["present_view"], "在场至少要有主角自己"
        assert data["present_view"][0]["name"] == data["player_name"]
        assert data["present_view"][0]["is_player"] is True
        assert all("portrait" in p for p in data["present_view"]), "没图也要给空串，前端靠它决定画不画"
