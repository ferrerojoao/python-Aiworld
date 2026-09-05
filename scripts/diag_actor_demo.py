"""Demonstrate how an NPC Actor call looks end to end.

The director (scripted here as FakeLLM) emits an `actor` beat for 朱明's
deep choice; run_turn then invokes run_actor; we print the exact input
messages and output, plus what the storyteller actually receives.
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

DIRECTOR_OUT = {
    "mode": "scene",
    "beats": [
        {"kind": "narrate", "text": "朱明听完你的问题，表情僵了一下。"},
        {
            "kind": "actor",
            "actor_npc_id": "npc_zhuming",
            "text": "被追问打架的旧事，朱明是含糊带过还是翻脸？",
            "meaning": "你问他昨天为什么打架，他想起他爸欠赌债的由头",
            "tone_hint": "好面子、心虚",
        },
    ],
    "lore_refs": [],
    "adopt_player_body": False,
    "private": False,
    "location": "net_bar",
    "participants": ["player", "npc_zhuming"],
}
ACTOR_OUT = {"decision": "含糊带过", "action_hint": "不接话，拉你去打街机", "tone": "不耐烦里带点心虚"}
STORY_OUT = {"prose": "朱明把头一偏，岔开话头……", "time_hint": None}
QC_OUT = {"status": "pass", "prose": "朱明把头一偏，岔开话头……", "issues": []}
AUDIT_OUT = {"hook_texts": [], "conflicts": [], "lifecycle": []}


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
            "玩家输入：": DIRECTOR_OUT,
            "剧情情境": ACTOR_OUT,
            "剧本指令：": STORY_OUT,
            "正文：": QC_OUT,
            "事件：": AUDIT_OUT,
        }
    )
    session = create_session(WORLD, SAVES, "actor_demo")
    runner = TurnRunner(session, llm, settings)

    candidate = await runner.run_turn("问朱明昨天为什么打架")

    print("=" * 72)
    print("回合流水线共调用 LLM:", len(llm.calls), "次")
    print("=" * 72)
    for call in llm.calls:
        who = "?"
        content = call["messages"][-1]["content"]
        system = call["messages"][0]["content"]
        if content.startswith("玩家输入"): who = "导演"
        elif "剧情情境" in content: who = "Actor"
        elif content.startswith("剧本指令"): who = "说书人"
        elif content.startswith("正文"): who = "质检"
        elif content.startswith("事件"): who = "审计"
        print(f"\n[{who}] model={call['model']} output=None (FakeLLM 不落 output，见 responses)")

    print()
    print("=" * 72)
    actor_call = next(c for c in llm.calls if "剧情情境" in c["messages"][-1]["content"])
    print("◆ Actor 的输入（messages）：")
    for msg in actor_call["messages"]:
        print(f"  [{msg['role']}]\n{msg['content']}\n")
    print("◆ Actor 的输出：", json.dumps(llm.responses["剧情情境"], ensure_ascii=False))
    print()
    print("◆ 说书人实际收到的剧本指令（beats_text，不含 actor 决策）：")
    story_call = next(c for c in llm.calls if "剧本指令" in c["messages"][-1]["content"])
    print(story_call["messages"][-1]["content"])

    shutil.rmtree(SAVES, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())