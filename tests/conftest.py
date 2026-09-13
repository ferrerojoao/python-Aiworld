from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session

# 测试夹具世界包：独立于 content/（用户可自由增删世界，测试不受影响）。
# 世界=存档 1:1 后 save.json 落在世界目录本身，所以 session 夹具把夹具包
# 拷进 tmp_path 再开档，绝不污染仓库里的 fixtures/。
WORLD_ROOT = Path(__file__).resolve().parent / "fixtures" / "qinghsi"

GENERIC_LLM_RESPONSE = {
    "prose": "朱明从网吧出来，看见你愣了一下，把烟头踩灭。",
    "time_hint": None,
    "summary": "朱明在网吧门口看见刘星，跟他打招呼。",
    "location": "主街",
    "participants": ["刘星", "朱明"],
    "private": False,
    "adopt_player_body": False,
    "actor_questions": [],
    "status": "pass",
    "issues": [],
    "decision": "打哈哈",
    "action_hint": "拉你去吃面",
    "tone": "随意",
    "conflicts": [],
    "reply": "好的，我会帮你安排。",
}


@pytest.fixture
def world_root(tmp_path: Path) -> Path:
    """可写的世界目录副本（世界=存档后开档会写 save.json 进世界目录）。"""
    root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, root)
    return root


@pytest.fixture
def session(world_root: Path):
    return create_session(world_root, "test")


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM({"*": GENERIC_LLM_RESPONSE})


@pytest.fixture
def settings() -> Settings:
    return Settings(content_root=WORLD_ROOT.parent, candidate_ttl_days=7)