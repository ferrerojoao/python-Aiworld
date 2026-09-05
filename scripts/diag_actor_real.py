"""Real-LLM check: actor deep-choice with the normalized persona prompt."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.llm import LLMGateway
from app.workers.actor import run_actor
from app.world.loader import load_world

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"


async def main() -> None:
    settings = get_settings()
    world = load_world(WORLD)
    llm = LLMGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        max_concurrency=2,
        timeout=300,
    )
    npc = world.npcs["npc_zhuming"]
    decision = await run_actor(
        llm,
        npc,
        "被追问打架的旧事，朱明是含糊带过还是翻脸？",
        context="玩家问朱明昨天为什么打架，朱明想起自己父亲欠赌债的由头，好面子、心虚",
        memories="2026-07-14T08:10:00 刘星来网吧看朱明打游戏，朱明让他等自己打完这把。",
        model=settings.resolved_model("actor"),
        temperature=0.8,
    )
    print("decision:", decision.decision)
    print("action_hint:", decision.action_hint)
    print("tone:", decision.tone)
    print("usage:", llm.get_usage())
    print("REAL ACTOR OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"REAL ACTOR FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)