"""重放探针：验证某一轮是否会派发 NPC Actor（真实模型取证）。

做法：把存档回退到目标轮之前（默认删 ev_00002、时钟回 08:00、场景回主街），
用真实 LLM 重跑同一句玩家输入，并在 run_writer / run_actor 上挂钩子记录
输入输出。只写临时目录，不动真实存档。

用法：
    python scripts/diag_actor_replay.py [玩家输入] [要丢弃的事件 id]

注意：模型 temperature=0.8，单次重放是近似复现而非原始那轮的逐字还原；
结论看"writer 是否产出 actor_questions"这一结构性信号。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.llm import LLMGateway
from app.core.presets import load_global_preset
from app.runtime import turn as turn_mod
from app.runtime.session import open_session

PLAYER_INPUT = sys.argv[1] if len(sys.argv) > 1 else "我去网吧，看看朱明在不在"
DROP_EVENT = sys.argv[2] if len(sys.argv) > 2 else "ev_00002"
SAVE_SRC = Path("content/qingshi2/saves/main")

tmp = Path(tempfile.mkdtemp(prefix="probe_actor_"))
dst = tmp / "main"
shutil.copytree(SAVE_SRC, dst)

kept = [
    line
    for line in (dst / "events.jsonl").read_text("utf-8").splitlines()
    if line.strip() and json.loads(line)["id"] != DROP_EVENT
]
(dst / "events.jsonl").write_text("\n".join(kept) + "\n", "utf-8")

save = json.loads((dst / "save.json").read_text("utf-8"))
save["clock"] = "2026-07-14T08:00:00"
save["player_scene"] = "主街"
save["meta"]["next_event_id"] = 2
save["active_lore_ids"] = []
(dst / "save.json").write_text(json.dumps(save, ensure_ascii=False, indent=2), "utf-8")

session = open_session(tmp, "main")
settings = get_settings()
llm = LLMGateway(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    max_concurrency=4,
    timeout=settings.llm_timeout_seconds,
)
preset = load_global_preset(settings.data_dir / "presets.json")

print("== 回退后状态 ==")
print("clock:", session.ledger.save.clock, "| scene:", session.ledger.save.player_scene)
print("在场@网吧:", session.ledger.present_at("网吧"))
print("朱明 has_actor:", session.world.npcs["朱明"].has_actor)
print()

log: dict[str, list] = {"writer": [], "actor": []}

_orig_writer = turn_mod.run_writer
_orig_actor = turn_mod.run_actor


async def writer_hook(*args, **kwargs):
    out = await _orig_writer(*args, **kwargs)
    questions = [
        q.model_dump() if hasattr(q, "model_dump") else dict(q)
        for q in (getattr(out, "actor_questions", None) or [])
    ]
    log["writer"].append(
        {
            "actor_questions": questions,
            "actor_decisions_in": kwargs.get("actor_decisions") or "",
            "prose_chars": len(getattr(out, "prose", "") or ""),
        }
    )
    return out


async def actor_hook(llm_, world, ledger, npc, question, **kwargs):
    decision = await _orig_actor(llm_, world, ledger, npc, question, **kwargs)
    log["actor"].append(
        {
            "npc": npc.id,
            "scene_id": kwargs.get("scene_id"),
            "question": question,
            "context": kwargs.get("context") or "",
            "system_prompt": kwargs.get("_system") or "(未捕获，见下)",
            "decision": getattr(decision, "decision", None),
            "action_hint": getattr(decision, "action_hint", None),
            "tone": getattr(decision, "tone", None),
        }
    )
    return decision


turn_mod.run_writer = writer_hook
turn_mod.run_actor = actor_hook

runner = turn_mod.TurnRunner(session, llm, settings, preset=preset)
candidate = asyncio.run(runner.run_turn(PLAYER_INPUT))

print("== writer 调用次数:", len(log["writer"]))
for i, w in enumerate(log["writer"], 1):
    print(f"  第{i}遍：prose {w['prose_chars']} 字 | actor_questions={w['actor_questions']}")
    if w["actor_decisions_in"]:
        print("         收到的 Actor 决策：", w["actor_decisions_in"][:200])

print()
print("== Actor 派发次数:", len(log["actor"]))
for a in log["actor"]:
    print(f"  NPC={a['npc']} 场景={a['scene_id']}")
    print(f"  问题：{a['question']}")
    print(f"  情境：{a['context'][:160]}")
    print(f"  决策：{a['decision']} | 行为：{a['action_hint']} | 语气：{a['tone']}")
print()
print("== 候选 conflicts（含 actor 未派发提示）:")
for item in candidate.conflicts or []:
    print("  ", item)
print()
print("临时存档目录:", tmp)
