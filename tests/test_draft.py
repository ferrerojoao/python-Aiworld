"""快速造世界：L1 种子包与 L2 一句话草稿（2026-09-13）。"""

from __future__ import annotations

import asyncio
import json

from app.core.llm import FakeLLM
from app.world.draft import (
    DEFAULT_START_SCENE,
    DraftLore,
    DraftNpc,
    DraftScene,
    WorldDraft,
    blank_world_assets,
    draft_to_assets,
    validate_assets,
)
from app.workers.drafter import run_world_draft

# 一份「形状合法、语义不过关」的草稿：世界书条目没有关键词。
# check_world 会因为"永远不会被命中"报问题，正好用来测一次修正那条路。
BAD_DRAFT = {
    "name": "草稿镇",
    "player_name": "刘星",
    "scenes": [{"id": "主街"}],
    "start_scene": "主街",
    "lorebook": [{"id": "坏条目", "keywords": [], "body": "没人会看到这段"}],
}

CLEAN_DRAFT = {
    "name": "草稿镇",
    "player_name": "刘星",
    "start_scene": "主街",
    "scenes": [
        {"id": "主街", "aliases": ["码头"], "perceivable": "石板路", "region": ""},
        {"id": "朱明家"},
    ],
    "npcs": [{"id": "朱明", "persona": "同桌", "has_actor": True}],
    "lorebook": [{"id": "镇子", "keywords": ["青石镇"], "body": "九十年代的县城"}],
}


# ---------------------------------------------------------------------------
# L1 种子
# ---------------------------------------------------------------------------


def test_blank_assets_are_minimal_and_playable():
    assets = blank_world_assets("newworld", "空白镇", "刘星", start_scene="主街")
    assert [s["id"] for s in assets["scenes"]] == ["主街"]
    assert assets["npcs"] == {"刘星": {"id": "刘星", "is_player": True}}
    assert assets["overview"]["name"] == "空白镇"
    assert assets["overview"]["start_scene"] == "主街"
    assert assets["lorebook"] == [] and assets["axes"] == []
    # 开场白不是占位符：它是主角在本世界的第一条位置事实
    assert assets["overview"]["opening"] == "刘星来到主街。"
    assert validate_assets(assets) == []


def test_blank_assets_fall_back_to_default_start_scene():
    assets = blank_world_assets("newworld", "", "刘星")
    assert assets["overview"]["name"] == "newworld"
    assert assets["overview"]["start_scene"] == DEFAULT_START_SCENE
    assert [s["id"] for s in assets["scenes"]] == [DEFAULT_START_SCENE]
    assert validate_assets(assets) == []


# ---------------------------------------------------------------------------
# 草稿 → 资产形状
# ---------------------------------------------------------------------------


def test_draft_to_assets_form_values_win():
    draft = WorldDraft(
        name="草稿名",
        start_scene="朱明家",
        player_name="草稿主角",
        scenes=[DraftScene(id="主街"), DraftScene(id="朱明家")],
        npcs=[DraftNpc(id="朱明", persona="同桌", has_actor=True)],
        lorebook=[DraftLore(id="镇子", keywords=["青石镇"], body="九十年代的县城")],
    )
    assets = draft_to_assets(draft, "qingshi3", name="表单名", player_name="刘星")
    assert assets["overview"]["name"] == "表单名"
    assert assets["overview"]["start_scene"] == "朱明家"
    assert set(assets["npcs"]) == {"刘星", "朱明"}
    assert assets["npcs"]["刘星"]["is_player"] is True
    assert assets["npcs"]["朱明"]["has_actor"] is True
    assert validate_assets(assets) == []


def test_draft_to_assets_player_card_survives_name_collision():
    draft = WorldDraft(
        scenes=[DraftScene(id="主街")],
        npcs=[DraftNpc(id="刘星", persona="草稿把主角写成了 NPC")],
    )
    assets = draft_to_assets(draft, "w", player_name="刘星")
    assert len(assets["npcs"]) == 1
    assert assets["npcs"]["刘星"]["is_player"] is True
    assert assets["npcs"]["刘星"]["persona"] == ""  # 草稿给它的人设被丢弃


def test_draft_to_assets_repairs_out_of_range_start_scene():
    draft = WorldDraft(start_scene="不存在的地方", scenes=[DraftScene(id="主街")])
    assets = draft_to_assets(draft, "w", player_name="刘星")
    assert assets["overview"]["start_scene"] == "主街"
    assert validate_assets(assets) == []


def test_draft_to_assets_survives_empty_draft():
    assets = draft_to_assets(WorldDraft(), "w", player_name="刘星")
    assert assets["overview"]["start_scene"] == DEFAULT_START_SCENE
    assert [s["id"] for s in assets["scenes"]] == [DEFAULT_START_SCENE]
    assert validate_assets(assets) == []


# ---------------------------------------------------------------------------
# 验收闸门
# ---------------------------------------------------------------------------


def test_validate_assets_reports_lore_without_keywords():
    assets = blank_world_assets("w", "W", "刘星")
    assets["lorebook"] = [
        {"id": "空条目", "keywords": [], "body": "x", "always_on": False}
    ]
    problems = validate_assets(assets)
    assert any("空条目" in p for p in problems)


def test_validate_assets_rejects_missing_player_card():
    assets = blank_world_assets("w", "W", "刘星")
    assets["npcs"] = {"朱明": {"id": "朱明"}}
    problems = validate_assets(assets)
    assert any("主角" in p for p in problems)


# ---------------------------------------------------------------------------
# 起草器
# ---------------------------------------------------------------------------


def _draft(llm, **kwargs):
    return asyncio.run(
        run_world_draft(llm, "县城高中暑假，我和同桌朱明", world_id="draft", **kwargs)
    )


def test_drafter_returns_assets_without_retry_when_clean():
    llm = FakeLLM({"*": CLEAN_DRAFT})
    draft, assets, problems = _draft(llm)
    assert problems == []
    assert len(llm.calls) == 1
    assert draft.name == "草稿镇"
    assert set(assets["npcs"]) == {"刘星", "朱明"}
    assert [s["id"] for s in assets["scenes"]] == ["主街", "朱明家"]


def test_drafter_retries_once_and_reports_remaining_problems():
    llm = FakeLLM({"*": BAD_DRAFT})
    _, assets, problems = _draft(llm)
    # 修正一次（共两次调用）；仍不合格就如实报出来，不假装通过
    assert len(llm.calls) == 2
    assert problems
    assert any("坏条目" in p for p in problems)
    retry_blob = json.dumps(llm.calls[1]["messages"], ensure_ascii=False)
    assert "坏条目" in retry_blob  # 引擎体检出的问题清单回灌给了模型
