"""Run one real-LLM turn and print the candidate + settlement.

Usage:
    python scripts/smoke_turn_real.py ["自拟玩家输入"]
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.llm import LLMGateway
from app.core.presets import load_global_preset
from app.runtime.session import create_session
from app.runtime.turn import TurnRunner

WORLD = Path(__file__).resolve().parent.parent / "content" / "qingshi2"
DEFAULT_INPUT = "去网吧找朱明，问他昨天为什么打架"


async def main() -> None:
    settings = get_settings()
    preset = load_global_preset(settings.data_dir / "presets.json")
    player_input = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT

    with tempfile.TemporaryDirectory() as td:
        # 始终在副本上跑：世界=存档 1:1，直接在 content/ 下开 session 会污染真实存档。
        # 必须同时丢掉 save.json 与 candidates/：带着真实存档会让本轮接着旧时钟跑，
        # 带着未采纳候选会直接撞 NeedChooseCandidate。
        world = Path(td) / WORLD.name
        shutil.copytree(
            WORLD,
            world,
            ignore=shutil.ignore_patterns("save.json", "candidates", "events.jsonl"),
        )
        session = create_session(world, "smoke")
        llm = LLMGateway(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            max_concurrency=2,
        )
        runner = TurnRunner(session, llm, settings, preset=preset)

        print(f"Running one real-LLM turn ... input={player_input!r}")
        print("clock before:", session.ledger.save.clock)
        candidate = await runner.run_turn(player_input)
        print()
        print("候选正文：")
        print(candidate.prose)
        print()
        print("副作用：", candidate.side_effects.model_dump())
        print("Usage:", llm.get_usage())

        # Adopt so the audit runs, then inspect what it settled.
        await runner.adopt(candidate.candidate_id)
        goals = session.ledger.save.goals
        print()
        print("审计后事件数:", len(session.ledger.narratives))
        print("剧情目标:", [(g.text[:30], g.status) for g in goals] or "（空）")
        print("clock after:", session.ledger.save.clock)
        print("结算留痕:", session.ledger.save.last_settlement)
        print("TURN SMOKE PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"TURN SMOKE FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)