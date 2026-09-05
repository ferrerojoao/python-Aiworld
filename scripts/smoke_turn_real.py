"""Run one real-LLM turn and print the candidate.

Usage:
    python scripts/smoke_turn_real.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.llm import LLMGateway
from app.core.presets import load_global_preset
from app.runtime.session import create_session
from app.runtime.turn import TurnRunner

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"


async def main() -> None:
    settings = get_settings()
    preset = load_global_preset(settings.data_dir / "presets.json")

    with tempfile.TemporaryDirectory() as td:
        session = create_session(WORLD, td, "smoke")
        llm = LLMGateway(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            max_concurrency=2,
        )
        runner = TurnRunner(session, llm, settings, preset=preset)

        print("Running one real-LLM turn ...")
        candidate = await runner.run_turn("去网吧找朱明，问他昨天为什么打架")
        print()
        print("候选正文：")
        print(candidate.prose)
        print()
        print("副作用：", candidate.side_effects.model_dump())
        print("Usage:", llm.get_usage())

        # Adopt so the audit runs, then inspect what it settled.
        await runner.adopt(candidate.candidate_id)
        hooks = session.ledger.save.hooks
        print()
        print("审计后事件数:", len(session.ledger.narratives))
        print("钩子台账:", [(h.text[:30], h.status) for h in hooks] or "（空）")
        print("TURN SMOKE PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"TURN SMOKE FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)