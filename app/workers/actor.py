from __future__ import annotations

from app.core.workorder import build_work_order
from app.ledger.queries import Ledger
from app.world.models import NpcCard, WorldContent
from app.workers.schemas import ActorDecision


def normalize_context(text: str, npc_name: str) -> str:
    """Rewrite writer-perspective context into the NPC actor's perspective.

    The writer describes the scene from the player's point of view, where
    "你" means the player and the NPC is "他/她/名字". The actor's work order
    already says "you are 朱明", so a raw context full of "你" would clash:
    the same pronoun would mean the player in one line and the character in
    the next. We mechanically swap "你/您" -> "玩家" so the remaining
    third-person references are unambiguous; the work order's pointer rules
    close the remaining gaps.
    """
    text = text.replace("您", "玩家").replace("你", "玩家")
    return text.strip()


async def run_actor(
    llm,
    world: WorldContent,
    ledger: Ledger,
    npc: NpcCard,
    question: str,
    *,
    context: str = "",
    scene_id: str | None = None,
    model: str = "fake",
    temperature: float = 0.8,
) -> ActorDecision:
    """NPC Actor for deep choices.

    The system prompt is a physically isolated work order assembled by
    ``build_work_order(viewer="actor_<id>")``: the actor sees only its own
    card (incl. persona patch), the current scene's perceptible area, and its
    own memory slice (already filtered by known_by visibility). It never sees
    private notes, other NPCs' secrets, goals, or the writer's motivation.
    """
    system = build_work_order(f"actor_{npc.id}", world, ledger, scene_id or "")
    scene = normalize_context(context, npc.id)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"深抉择问题：{question}\n你当前身处的情境（编剧旁观视角）：\n{scene}",
        },
    ]
    data = await llm.complete_json(
        messages,
        ActorDecision,
        model=model,
        temperature=temperature,
    )
    return ActorDecision.model_validate(data)