"""Direct end-to-end engine check (bypasses pytest tmp machinery).
Runs a full turn pipeline with FakeLLM against a scratch save dir
inside the workspace, then adopts and verifies the ledger.
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session
from app.runtime.turn import TurnRunner

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "scratch_saves"
if SAVES.exists():
    shutil.rmtree(SAVES, ignore_errors=True)

RESP = {
    "mode": "scene",
    "beats": [{"kind": "narrate", "text": "朱明从网吧出来，看见你愣了一下。"}],
    "lore_refs": [],
    "motivation_note": "",
    "adopt_player_body": False,
    "private": False,
    "location": "net_bar",
    "participants": ["player", "npc_zhuming"],
    "prose": "朱明从网吧出来，看见你愣了一下，把烟头踩灭，问你吃饭了没。",
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
    settings = Settings(content_root=Path("content"))
    llm = FakeLLM({"*": RESP})
    session = create_session(WORLD, SAVES, "e2e")

    runner = TurnRunner(session, llm, settings)

    # 1. A chat turn -> pending candidate, no new ledger events yet.
    c1 = await runner.run_turn("去网吧找朱明，问他昨天为什么打架")
    print("1. candidate prose:", c1.prose[:40])
    print("   side_effects:", c1.side_effects.model_dump())
    assert c1.prose and c1.side_effects.narrative
    assert len(session.ledger.events) == 1, "only the opening event before adopt"
    assert session.ledger.events[0]["source"] == "opening"

    # 2. Reroll keeps both versions.
    c2 = await runner.reroll(c1.turn_id, mode="rephrase", note="柔和一点")
    assert len(session.candidates.list_for_turn(c1.turn_id)) == 2
    print("2. reroll ok, two candidates:", [c.candidate_id for c in session.candidates.list_for_turn(c1.turn_id)])

    # 3. Adopt second -> narrative lands in events.jsonl, candidates cleaned.
    await runner.adopt(c2.candidate_id)
    assert len(session.ledger.narratives) == 2  # opening + adopted turn
    assert session.ledger.narratives[-1]["body"] == c2.prose
    assert session.ledger.narratives[-1]["player_input"] is not None
    assert session.candidates.list_pending() == []
    printed = Path(SAVES / "e2e" / "events.jsonl").read_text(encoding="utf-8")
    assert "narrative" in printed
    print("3. adopt ok; events.jsonl written; candidates cleaned")

    # 4. Clock advanced by move delta (main_street -> net_bar = 1 edge = 10min).
    assert session.ledger.save.clock == "2026-07-14T08:10:00", session.ledger.save.clock
    print("4. clock advanced to", session.ledger.save.clock)

    # 5. Query route bypasses LLM.
    c3 = await runner.run_turn("现在什么时辰")
    assert c3.mode == "query" and c3.side_effects.narrative is None
    print("5. query bypass ok:", c3.prose)

    # 6. Next input auto-adopts single pending candidate (the query candidate
    #    commits as a no-op since it carries no narrative side effects).
    c4 = await runner.run_turn("然后呢")
    assert len(session.ledger.narratives) == 2  # opening + adopted turn
    assert len(session.candidates.list_pending()) == 1  # the new turn's candidate
    print("6. auto-adopt ok; narratives:", len(session.ledger.narratives))

    print("\nALL E2E CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())