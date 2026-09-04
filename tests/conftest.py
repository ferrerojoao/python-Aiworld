from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session

WORLD_ROOT = Path(__file__).resolve().parent.parent / "content" / "qinghsi"

GENERIC_LLM_RESPONSE = {
    "mode": "scene",
    "beats": [{"kind": "narrate", "text": "朱明从网吧出来，看见你愣了一下。"}],
    "lore_refs": [],
    "motivation_note": "",
    "adopt_player_body": False,
    "private": False,
    "location": "main_street",
    "participants": ["player", "npc_zhuming"],
    "prose": "朱明从网吧出来，看见你愣了一下，把烟头踩灭。",
    "time_hint": None,
    "status": "pass",
    "issues": [],
    "decision": "含糊带过",
    "action_hint": "拉你去打街机",
    "tone": "敷衍",
    "hook_texts": [],
    "conflicts": [],
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
    return Settings(content_root=Path("content"), candidate_ttl_days=7)