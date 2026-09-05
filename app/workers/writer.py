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
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"玩家输入：{player_input}"},
    ]
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