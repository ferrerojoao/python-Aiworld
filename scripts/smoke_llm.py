"""Smoke test for a real OpenAI-compatible LLM endpoint.

Usage:
    python scripts/smoke_llm.py

It reads AIWORLD_LLM_BASE_URL, AIWORLD_LLM_API_KEY,
AIWORLD_MODEL_MAIN / AIWORLD_MODEL_CHEAP from the environment.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel

from app.config import get_settings
from app.core.llm import LLMGateway


class PingResult(BaseModel):
    message: str


async def main() -> None:
    settings = get_settings()
    print(f"Base URL : {settings.llm_base_url}")
    print(f"Main model: {settings.resolved_model('director')}")
    print(f"Cheap model: {settings.resolved_model('qc')}")
    print()

    gateway = LLMGateway(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        max_concurrency=2,
    )

    print("1) Testing complete_json ...")
    data = await gateway.complete_json(
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with JSON {\"message\": \"hello\"}."},
        ],
        PingResult,
        model=settings.resolved_model("director"),
        temperature=0.2,
    )
    print("   JSON OK:", data)
    print()

    print("2) Testing complete_text ...")
    text = await gateway.complete_text(
        [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in one short sentence."},
        ],
        model=settings.resolved_model("director"),
        temperature=0.7,
    )
    print("   Text OK:", text)
    print()

    print("Usage:", gateway.get_usage())
    print("SMOKE PASSED")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)