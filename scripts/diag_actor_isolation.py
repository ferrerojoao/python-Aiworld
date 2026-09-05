"""Verify the actor's physical isolation: its work order must never contain
private notes, other NPCs' private events, hooks, conflicts or lore
candidates — only its own persona, the scene and its own memory slice."""
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.workorder import build_work_order
from app.ledger.save import Conflict, Hook
from app.runtime.session import create_session

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "scratch_saves"
if SAVES.exists():
    shutil.rmtree(SAVES, ignore_errors=True)


async def main() -> None:
    session = create_session(WORLD, SAVES, "isolation")
    ledger = session.ledger

    # 朱明的一条公开经历（他自己会记得）
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T08:10:00",
            "location": "net_bar",
            "participants": ["player", "npc_zhuming"],
            "known_by": None,
            "body": "朱明请刘星喝可乐。",
            "summary": "朱明请刘星喝可乐。",
            "player_input": "跟着进队",
            "source": "turn",
        }
    )
    # 王蓉的一条私密事件（朱明绝不能知道）
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T08:30:00",
            "location": "alley_old_building",
            "participants": ["player", "npc_wangrong"],
            "known_by": ["player", "npc_wangrong"],
            "body": "王蓉在老巷告诉刘星，她爸爸的修车铺今年要关门。",
            "summary": "王蓉私下说起自家修车铺要关门。",
            "player_input": "问她家的事",
            "source": "turn",
        }
    )
    # 钩子 + 矛盾（Actor 一律不该看到）
    ledger.save.hooks.append(Hook(id="hk_1", text="朱明答应教刘星打游戏", status="open"))
    ledger.save.pending_conflicts.append(Conflict(id="cf_1", desc="朱明自称从不去网吧"))
    ledger.persist_save()

    order = build_work_order("actor_npc_zhuming", session.world, ledger, "net_bar")

    must_have = ["你正在扮演：朱明", "人格：", "喝可乐", "当前场景", "你记得的事"]
    must_not = ["王蓉", "修车铺要关门", "他爸在县城欠了赌债", "教刘星打游戏", "自称从不去网吧", "世界书候选", "事件日志", "编剧准则", "开放钩子", "待澄清矛盾"]

    ok = True
    for needle in must_have:
        found = needle in order
        print(f"{'OK ' if found else 'MISS'} 应有: {needle}")
        ok = ok and found
    for needle in must_not:
        bad = needle in order
        print(f"{'OK ' if not bad else 'LEAK!'} 不得出现: {needle}")
        ok = ok and not bad

    print("\n--- Actor 工作单全文 ---")
    print(order)
    assert ok, "ACTOR ISOLATION VIOLATED"
    print("\nACTOR ISOLATION VERIFY PASSED")

    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())