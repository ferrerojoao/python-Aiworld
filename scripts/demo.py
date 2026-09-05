"""Offline demo using FakeLLM, no network required.

Run:
    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session
from app.runtime.turn import TurnRunner

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "content" / "qinghsi" / "saves"


async def main() -> None:
    save_dir = SAVES / "demo"
    if save_dir.exists():
        shutil.rmtree(save_dir)

    session = create_session(WORLD, SAVES, "demo")
    settings = Settings(content_root=Path("content"))
    llm = FakeLLM(
        {
            "*": {
                "prose": "朱明在网吧门口看到你，把烟头踩灭，问你吃饭了没。",
                "time_hint": None,
                "summary": "朱明在网吧门口看见刘星。",
                "location": "net_bar",
                "participants": ["player", "npc_zhuming"],
                "private": False,
                "adopt_player_body": False,
                "actor_questions": [],
                "status": "pass",
                "issues": [],
                "decision": "打哈哈",
                "action_hint": "拉你去吃面",
                "tone": "随意",
                "hook_texts": [],
                "conflicts": [],
            }
        }
    )
    runner = TurnRunner(session, llm, settings)
    try:
        candidate = await runner.run_turn("去网吧找朱明，问他昨天为什么打架")
        print("候选 1:", candidate.prose)

        candidate2 = await runner.reroll(candidate.turn_id, mode="rephrase", note="柔和一点")
        print("候选 2:", candidate2.prose)

        await runner.adopt(candidate2.candidate_id)
        print("采纳后事件数:", len(session.ledger.narratives))
        print("采纳后候选数:", len(session.candidates.list_pending()))
    finally:
        shutil.rmtree(save_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())