"""Reproduce the StoryOutput validation failure the user hit with a real LLM:
the model answered {"directives": []} instead of {"prose": "..."}."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.llm import FakeLLM
from app.workers.storyteller import run_storyteller
from app.workers.schemas import Directive, Beat
from app.world.loader import load_world

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"


class BadShapeLLM(FakeLLM):
    """FakeLLM that always answers with the wrong shape the real model gave."""

    async def complete_json(self, messages, schema, *, model="fake", temperature=0.2):
        return {"directives": []}


async def main() -> None:
    world = load_world(WORLD)
    directive = Directive(
        mode="scene",
        beats=[
            Beat(kind="narrate", text="朱明从网吧出来，看见你愣了一下。"),
            Beat(kind="speech", speaker="npc_zhuming", meaning="不想提打架的事", tone_hint="敷衍"),
        ],
        location="net_bar",
        participants=["player", "npc_zhuming"],
    )
    try:
        await run_storyteller(BadShapeLLM(), world, directive, model="fake")
        print("UNEXPECTED: storyteller did not fail")
    except Exception as exc:  # noqa: BLE001
        print(f"REPRODUCED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())