"""Snapshot the director's work order to verify ledger data is included."""
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session, write_opening_event
from app.workers.writer import run_writer

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "scratch_saves"
if SAVES.exists():
    shutil.rmtree(SAVES, ignore_errors=True)


async def main() -> None:
    session = create_session(WORLD, SAVES, "wo")
    # Plant some history so the work order has events to show.
    evs = [
        ("2026-07-14T08:10:00", "net_bar", ["player", "npc_zhuming"], "朱明让刘星等他打完这把。", "去网吧找朱明"),
        ("2026-07-14T08:15:00", "net_bar", ["player", "npc_zhuming"], "朱明请你喝可乐，还拉你进队打游戏。", "跟着进队"),
    ]
    for i, (at, loc, parts, body, pinput) in enumerate(evs, start=2):
        session.ledger.append(
            {
                "id": f"ev_{i:05d}",
                "kind": "narrative",
                "at": at,
                "location": loc,
                "participants": parts,
                "known_by": None,
                "body": body,
                "summary": body,
                "player_input": pinput,
                "source": "turn",
            }
        )
    # an open hook + a pending conflict
    from app.ledger.save import Conflict, Hook

    session.ledger.save.hooks.append(Hook(id="hk_1", text="朱明答应明天教刘星打游戏", status="open"))
    session.ledger.save.pending_conflicts.append(Conflict(id="cf_1", desc="朱明自称从不去网吧，但有人看见他常去"))
    session.ledger.persist_save()

    fake = FakeLLM(
        {
            "*": {
                "prose": "朱明抬头看你。",
                "time_hint": None,
                "summary": "朱明抬头看见刘星。",
                "location": "net_bar",
                "participants": ["player", "npc_zhuming"],
                "private": False,
                "adopt_player_body": False,
                "actor_questions": [],
            }
        }
    )
    out = await run_writer(
        fake,
        session.world,
        session.ledger,
        "问朱明昨天为什么打架",
        scene_id="net_bar",
        preset=session.world.presets,
    )
    assert out.prose

    system = fake.calls[0]["messages"][0]["content"]
    sections = ["事件日志", "在场 NPC 近况", "幕后注", "世界书候选", "开放钩子", "待澄清矛盾"]
    for name in sections:
        found = name in system
        print(f"{'OK ' if found else 'MISS'} {name}")
        assert found, f"missing work-order section: {name}"
    for needle in ["等他打完这把", "拉你进队打游戏", "朱明答应明天教刘星打游戏", "自称从不去网吧", "他爸在县城欠了赌债", "镇上唯一的网吧"]:
        found = needle in system
        print(f"{'OK ' if found else 'MISS'} 内容: {needle}")
        assert found, f"missing content: {needle}"
    idx = system.find("事件日志")
    print("\n--- 事件日志段预览 ---")
    print(system[idx : idx + 240])
    print("\nWORK ORDER VERIFY PASSED")

    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())