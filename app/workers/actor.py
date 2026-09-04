from __future__ import annotations

from app.world.models import NpcCard
from app.workers.schemas import ActorDecision


async def run_actor(
    llm,
    npc: NpcCard,
    question: str,
    *,
    context: str = "",
    model: str = "fake",
    temperature: float = 0.8,
) -> ActorDecision:
    """NPC Actor for deep choices.

    The full implementation must receive a physically isolated work order.
    This MVP keeps the protocol and a minimal prompt.
    """
    system = (
        f"你正在扮演 {npc.name}。只根据你自己知道的信息做决定。\n"
        f"人格：{npc.persona}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"深抉择：{question}\n情境：{context}"},
    ]
    data = await llm.complete_json(
        messages,
        ActorDecision,
        model=model,
        temperature=temperature,
    )
    return ActorDecision.model_validate(data)