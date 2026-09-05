"""Print the exact prompts used in a turn via TraceRecorder + FakeLLM."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.core.llm import FakeLLM
from app.core.presets import load_global_preset
from app.runtime.session import create_session
from app.runtime.trace import TraceRecorder
from app.runtime.turn import TurnRunner

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"

GENERIC = {
    "mode": "scene",
    "beats": [{"kind": "narrate", "text": "朱明从网吧出来，看见你愣了一下。"}],
    "lore_refs": [],
    "adopt_player_body": False,
    "private": False,
    "location": "net_bar",
    "participants": ["player", "npc_zhuming"],
    "prose": "朱明从网吧出来，看见你愣了一下。",
    "time_hint": None,
    "status": "pass",
    "issues": [],
    "decision": "打哈哈",
    "action_hint": "拉你去吃面",
    "tone": "随意",
    "hook_texts": [],
    "conflicts": [],
}


async def main() -> None:
    settings = Settings(content_root=Path("content"), data_dir=Path("data"))
    preset = load_global_preset(settings.data_dir / "presets.json")
    with tempfile.TemporaryDirectory() as td:
        session = create_session(WORLD, td, "diag")
        trace = TraceRecorder(FakeLLM({"*": GENERIC}))
        runner = TurnRunner(session, trace, settings, preset=preset)
        await runner.run_turn("去网吧，看看朱明在不在")

        for i, entry in enumerate(trace.entries, 1):
            print(f"\n===== LLM call {i}: {entry['type']} =====")
            for msg in entry["messages"]:
                print(f"\n--- {msg['role']} ---")
                print(msg["content"])
            if "output" in entry:
                print("\n--- OUTPUT ---")
                print(entry["output"])


if __name__ == "__main__":
    asyncio.run(main())