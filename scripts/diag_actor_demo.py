"""Demonstrate the merged writer + deep-choice actor loop.

The writer (single agent) first emits actor_questions for 朱明's deep
choice; turn.py runs the isolated Actor and re-invokes the writer with the
decision. We print the exact calls and the final candidate.
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.core.llm import FakeLLM
from app.runtime.session import create_session
from app.runtime.turn import TurnRunner

WORLD = Path(__file__).resolve().parent.parent / "content" / "qinghsi"
SAVES = Path(__file__).resolve().parent.parent / "scratch_saves"
if SAVES.exists():
    shutil.rmtree(SAVES, ignore_errors=True)

WRITER_FIRST = {
    "prose": "朱明听完你的问题，表情僵了一下，没接话。",
    "time_hint": None,
    "summary": "刘星追问打架的事，朱明回避。",
    "location": "net_bar",
    "participants": ["player", "npc_zhuming"],
    "private": False,
    "adopt_player_body": False,
    "actor_questions": [
        {
            "npc_id": "npc_zhuming",
            "question": "被追问打架的旧事，朱明是含糊带过还是翻脸？",
            "context": "玩家问朱明昨天为什么打架，朱明想起他爸欠赌债的由头，好面子、心虚",
        }
    ],
}
WRITER_SECOND = {
    "prose": "朱明把脸别过去，含糊地说了句「哥，那事别提了」，起身去买了两瓶可乐。",
    "time_hint": None,
    "summary": "朱明含糊带过，岔开话题请刘星喝可乐。",
    "location": "net_bar",
    "participants": ["player", "npc_zhuming"],
    "private": False,
    "adopt_player_body": False,
    "actor_questions": [],
}
ACTOR_OUT = {"decision": "含糊带过", "action_hint": "岔开话题，去买两瓶可乐", "tone": "不耐烦里带点心虚"}
QC_OUT = {"status": "pass", "prose": "朱明把脸别过去，含糊地说了句「哥，那事别提了」，起身去买了两瓶可乐。", "issues": []}
AUDIT_OUT = {
    "location": "net_bar",
    "participants": ["player", "npc_zhuming"],
    "private": False,
    "delta_minutes": 0,
    "hook_texts": [],
    "closed_hook_ids": [],
    "lifecycle": [],
}


class PrefixKeyLLM(FakeLLM):
    """Match responses by the first user-message prefix, not exact key."""

    def _key(self, messages):
        for msg in reversed(messages):
            if msg.get("role") in {"user", "system"}:
                content = msg.get("content", "")
                for prefix in self.responses:
                    if content.startswith(prefix):
                        return prefix
        return "*"


async def main() -> None:
    settings = Settings(content_root=Path("content"))
    llm = PrefixKeyLLM(
        {
            "玩家输入：": WRITER_FIRST,
            "深抉择问题": ACTOR_OUT,
            "以下深抉择已由对应 NPC 亲自决定": WRITER_SECOND,
            "正文：": QC_OUT,
            "事件：": AUDIT_OUT,
        }
    )
    session = create_session(WORLD, SAVES, "actor_demo")
    runner = TurnRunner(session, llm, settings)

    candidate = await runner.run_turn("问朱明昨天为什么打架")

    print("=" * 72)
    print("回合流水线共调用 LLM:", len(llm.calls), "次")
    for call in llm.calls:
        content = call["messages"][-1]["content"]
        system = call["messages"][0]["content"]
        if content.startswith("玩家输入"): who = "writer#1（编排+成文）"
        elif "深抉择问题" in content: who = "Actor（朱明深抉择）"
        elif "深抉择已由" in content: who = "writer#2（按决策成文）"
        elif content.startswith("正文"): who = "质检"
        elif content.startswith("事件"): who = "审计"
        elif "改写要求" in content: who = "writer（重掷）"
        else: who = "?"
        print(f"[{who}]")
        print("  system 头部:", system.splitlines()[0][:60])
        print("  user:", content.replace("\n", " ")[:120])
    print("=" * 72)

    actor_call = next(c for c in llm.calls if "深抉择问题" in c["messages"][-1]["content"])
    print("◆ Actor 输入 [system]:")
    print(actor_call["messages"][0]["content"])
    print("◆ Actor 输出:", json.dumps(llm.responses["深抉择问题"], ensure_ascii=False))

    second = next(c for c in llm.calls if "深抉择已由" in c["messages"][-1]["content"])
    print("\n◆ writer#2 收到的决策块:")
    print(second["messages"][-1]["content"][:200])

    print("\n◆ 最终候选正文:")
    print(candidate.prose)
    assert candidate.prose and candidate.side_effects.narrative["summary"]
    print("\nALL ACTOR DEMO CHECKS PASSED")

    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())