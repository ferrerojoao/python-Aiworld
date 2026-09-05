"""Reproduce the WriterOutput validation failure the user hit with a real LLM:
the model answered {"directives": []} instead of {"prose": "..."}."""
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.llm import FakeLLM
from app.runtime.session import create_session
from app.workers.writer import run_writer

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "scratch_saves"
if SAVES.exists():
    shutil.rmtree(SAVES, ignore_errors=True)


class BadShapeLLM(FakeLLM):
    """FakeLLM that always answers with the wrong shape the real model gave."""

    async def complete_json(self, messages, schema, *, model="fake", temperature=0.2):
        return {"directives": []}


async def main() -> None:
    session = create_session(WORLD, SAVES, "shape")
    try:
        await run_writer(BadShapeLLM(), session.world, session.ledger, "去网吧找朱明", scene_id="net_bar")
        print("UNEXPECTED: writer did not fail")
    except Exception as exc:  # noqa: BLE001
        print(f"REPRODUCED: {type(exc).__name__}: {exc}")
    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())