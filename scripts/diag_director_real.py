"""Debug what the real director agent actually returns."""

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
from app.workers.director import run_director

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"


async def main() -> None:
    settings = get_settings()
    preset = load_global_preset(settings.data_dir / "presets.json")
    with tempfile.TemporaryDirectory() as td:
        session = create_session(WORLD, td, "diag")
        llm = LLMGateway(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            max_concurrency=2,
        )
        directive = await run_director(
            llm,
            session.world,
            session.ledger,
            "去网吧找朱明，问他昨天为什么打架",
            rule_bundle={"route": "move", "scene": "main_street", "destination": "net_bar", "delta_minutes": 10},
            scene_id="main_street",
            preset=preset,
            model=settings.resolved_model("director"),
            temperature=0.7,
        )
        print("=== Director raw parsed directive ===")
        print(directive.model_dump())
        print()
        print("beats count:", len(directive.beats))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"DIAG FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)