from __future__ import annotations

import json
import shutil

from app.world.loader import check_world, load_world
from app.world.models import PLAYER_PLACEHOLDER


def test_load_qinghsi(world_root):
    world = load_world(world_root)
    assert world.meta.id == "qinghsi"
    assert len(world.scenes) >= 3
    assert "朱明" in world.npcs
    assert world.npcs["朱明"].has_actor is True


def test_check_world_ok(world_root):
    assert check_world(world_root) == []


def test_player_is_an_npc_card(world_root):
    """主角 = 人物表里 is_player 的那张卡（2026-09-13 用户拍板）。"""
    world = load_world(world_root)
    card = world.player()
    assert card is not None
    assert card.id == "刘星"
    assert card.is_player is True
    assert card.has_actor is False  # 主角不配 Actor
    assert world.player_name() == "刘星"
    # 主角与 NPC 同构：字段齐备（含听域 region）
    assert card.region == []
    assert "刘星" in world.npcs


def test_start_scene_resolution(world_root):
    """开局场景：start_scene 命中注册场景才作数，否则取第一个注册场景。"""
    world = load_world(world_root)
    assert world.meta.start_scene == "主街"
    assert world.start_scene_id() == "主街"

    world.meta.start_scene = "不存在的地方"
    assert world.start_scene_id() == world.scenes[0].id

    world.meta.start_scene = ""
    assert world.start_scene_id() == world.scenes[0].id


def _copy_world(world_root, tmp_path):
    dest = tmp_path / "world"
    shutil.copytree(world_root, dest, ignore=shutil.ignore_patterns("saves"))
    return dest


def test_missing_player_card_is_synthesized(world_root, tmp_path):
    """世界包没写主角卡时补一张占位卡——引擎处处以主角名为键，缺卡会静默失配。"""
    dest = _copy_world(world_root, tmp_path)
    (dest / "npcs" / "刘星.json").unlink()

    world = load_world(dest)
    assert world.player_name() == PLAYER_PLACEHOLDER
    assert world.npcs[PLAYER_PLACEHOLDER].is_player is True
    # 校验层会提示改真名（不是错误，是可用的占位态）
    assert any("占位名" in p for p in check_world(dest))

def test_check_world_flags_duplicate_player_and_bad_start_scene(world_root, tmp_path):
    dest = _copy_world(world_root, tmp_path)
    zm = json.loads((dest / "npcs" / "朱明.json").read_text(encoding="utf-8"))
    zm["is_player"] = True
    (dest / "npcs" / "朱明.json").write_text(
        json.dumps(zm, ensure_ascii=False), encoding="utf-8"
    )
    overview = json.loads((dest / "world.json").read_text(encoding="utf-8"))
    overview["start_scene"] = "不存在的地方"
    (dest / "world.json").write_text(
        json.dumps(overview, ensure_ascii=False), encoding="utf-8"
    )

    problems = check_world(dest)
    assert any("主角卡只能有一张" in p for p in problems)
    assert any("start_scene 不在场景表里" in p for p in problems)


def test_save_world_assets_requires_exactly_one_player(world_root, tmp_path):
    """服务端兜底：保存世界资产时没有主角卡（被删掉）→ 拒绝，且不落盘。"""
    from app.world.loader import save_world_assets

    dest = _copy_world(world_root, tmp_path)
    world = load_world(dest)
    payload = {
        "overview": world.meta.model_dump(),
        "lorebook": [e.model_dump() for e in world.lorebook],
        "scenes": [s.model_dump() for s in world.scenes],
        "npcs": {"朱明": world.npcs["朱明"].model_dump()},
        "axes": [],
    }
    try:
        save_world_assets(dest, payload)
        raise AssertionError("应当拒绝没有主角卡的世界资产")
    except ValueError as exc:
        assert "主角卡" in str(exc)
    # 校验先于落盘：被拒后原世界文件仍在（主角卡没被删掉）
    assert (dest / "npcs" / "刘星.json").exists()
