"""Real-LLM check: actor deep-choice with the physically isolated work order."""
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.llm import LLMGateway
from app.runtime.session import create_session
from app.workers.actor import run_actor

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "scratch_saves"
if SAVES.exists():
    shutil.rmtree(SAVES, ignore_errors=True)


async def main() -> None:
    settings = get_settings()
    session = create_session(WORLD, SAVES, "actor_real")
    llm = LLMGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        max_concurrency=2,
        timeout=300,
    )
    npc = session.world.npcs["npc_zhuming"]
    decision = await run_actor(
        llm,
        session.world,
        session.ledger,
        npc,
        "被追问打架的旧事，朱明是含糊带过还是翻脸？",
        context="玩家问朱明昨天为什么打架，朱明想起自己父亲欠赌债的由头，好面子、心虚",
        scene_id="net_bar",
        model=settings.resolved_model("actor"),
        temperature=0.8,
    )
    print("decision:", decision.decision)
    print("action_hint:", decision.action_hint)
    print("tone:", decision.tone)
    print("usage:", llm.get_usage())
    print("REAL ACTOR OK")
    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"REAL ACTOR FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)