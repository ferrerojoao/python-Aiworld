from __future__ import annotations

from app.core.workorder import build_work_order
from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import WriterOutput


async def run_writer(
    llm,
    world: WorldContent,
    ledger: Ledger,
    player_input: str,
    *,
    rule_bundle: dict | None = None,
    scene_id: str | None = None,
    preset: NarrativePreset | None = None,
    actor_decisions: str = "",
    rewrite_note: str = "",
    writer_directive: str = "",
    model: str = "fake",
    temperature: float = 0.8,
) -> WriterOutput:
    """Single-agent writer: decides the scene and writes the prose in one call.

    The work order (objective snapshot + subjective contract) is assembled by
    ``app.core.workorder.build_work_order``. When the output carries
    actor_questions, the caller runs the isolated Actor for each and
    re-invokes with actor_decisions set.
    """
    system = build_work_order("writer", world, ledger, scene_id or "", preset=preset)
    if player_input.strip():
        player_msg = {"role": "user", "content": f"玩家输入：{player_input}"}
    else:
        player_msg = {
            "role": "user",
            "content": "玩家输入：（本轮无行动，按导演要求与当前情境自然推进）",
        }
    messages = [
        {"role": "system", "content": system},
        player_msg,
    ]
    if writer_directive:
        messages.append(
            {
                "role": "user",
                "content": "本回合导演要求（必须执行，但不写进正文、不算玩家台词、不进事件日志）：\n"
                + writer_directive,
            }
        )
    if rule_bundle:
        messages.append({"role": "user", "content": f"规则段预结算：{rule_bundle}"})
    if actor_decisions:
        messages.append({"role": "user", "content": f"以下深抉择已由对应 NPC 亲自决定，按此成文：\n{actor_decisions}"})
    if rewrite_note:
        messages.append({"role": "user", "content": f"改写要求：{rewrite_note}"})

    data = await llm.complete_json(
        messages,
        WriterOutput,
        model=model,
        temperature=temperature,
    )
    return WriterOutput.model_validate(data)