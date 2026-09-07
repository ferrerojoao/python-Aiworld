"""Snapshot the director's work order to verify ledger data is included."""
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session, write_opening_event
from app.world.models import NarrativePreset
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
    # an active goal for the director to see
    from app.ledger.save import Goal

    session.ledger.save.goals.append(Goal(id="goal_1", text="查明朱明打架的真相", kind="big", npc_id="npc_zhuming"))
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
        preset=NarrativePreset(writer_guidelines="测试编剧准则：克制写实。"),
    )
    assert out.prose

    system = fake.calls[0]["messages"][0]["content"]
    sections = ["事件日志", "在场 NPC 近况", "幕后注", "世界书候选", "开放钩子"]
    for name in sections:
        found = name in system
        print(f"{'OK ' if found else 'MISS'} {name}")
        assert found, f"missing work-order section: {name}"
    for needle in ["等他打完这把", "拉你进队打游戏", "朱明答应明天教刘星打游戏", "他爸在县城欠了赌债", "镇上唯一的网吧"]:
        found = needle in system
        print(f"{'OK ' if found else 'MISS'} 内容: {needle}")
        assert found, f"missing content: {needle}"
    assert "待澄清矛盾" not in system, "conflict block should be gone"
    idx = system.find("事件日志")
    print("\n--- 事件日志段预览 ---")
    print(system[idx : idx + 240])

    # Order assertions for the 2026-09-05 continuation-first assembly.
    # Use collision-free anchors ("事件日志" also appears in the golden rules).
    order_checks = [
        ("金科玉律", "玩家资料"),
        ("金科玉律", "编剧准则"),
        ("编剧准则", "玩家资料"),
        ("当前场景", "最近剧情（原文）"),  # scene snapshot before the recent prose
        ("更早事件（摘要）", "最近剧情（原文）"),  # summaries before prose
        ("输出必须是 JSON 对象", "最近剧情（原文）"),  # output format before the log
    ]
    for earlier, later in order_checks:
        ok = system.find(earlier) < system.find(later)
        print(f"{'OK ' if ok else 'FAIL'} 顺序: {earlier} 在 {later} 之前")
        assert ok, f"order broken: {earlier} should precede {later}"
    print("\nWORK ORDER VERIFY PASSED")

    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())