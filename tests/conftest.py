from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session

# 测试夹具世界包：独立于 content/（用户可自由增删世界，测试不受影响）。
WORLD_ROOT = Path(__file__).resolve().parent / "fixtures" / "qinghsi"

GENERIC_LLM_RESPONSE = {
    "prose": "朱明从网吧出来，看见你愣了一下，把烟头踩灭。",
    "time_hint": None,
    "summary": "朱明在网吧门口看见刘星，跟他打招呼。",
    "location": "主街",
    "participants": ["player", "朱明"],
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
def world_root() -> Path:
    return WORLD_ROOT


@pytest.fixture
def save_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def session(world_root: Path, save_root: Path):
    return create_session(world_root, save_root, "test")


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM({"*": GENERIC_LLM_RESPONSE})


@pytest.fixture
def settings() -> Settings:
    return Settings(content_root=WORLD_ROOT.parent, candidate_ttl_days=7)