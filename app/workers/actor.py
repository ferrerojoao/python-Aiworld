from __future__ import annotations

from app.world.models import NpcCard
from app.workers.schemas import ActorDecision


def normalize_context(text: str, npc_name: str) -> str:
    """Rewrite director-perspective context into the NPC actor's perspective.

    The director writes context from their own / the player's point of view,
    where "你" means the player and the NPC is "他/她/名字". The actor's
    prompt already says "you are 朱明", so a raw context full of "你" would
    clash: the same pronoun would mean the player in one line and the
    character in the next. We mechanically swap "你/您" -> "玩家" so the
    remaining third-person references (the NPC's own name, other NPCs) are
    unambiguous, and the pointer rules below close the remaining gaps.
    """
    text = text.replace("您", "玩家").replace("你", "玩家")
    return text.strip()


async def run_actor(
    llm,
    npc: NpcCard,
    question: str,
    *,
    context: str = "",
    memories: str = "",
    model: str = "fake",
    temperature: float = 0.8,
) -> ActorDecision:
    """NPC Actor for deep choices.

    The actor receives only what the character could know:
    - their own persona/card (never private notes or director intent),
    - their own memory slice,
    - the scene as described from the director's outside view.
    """
    scene = normalize_context(context, npc.name)
    memory_block = f"\n你记得的事：\n{memories}" if memories else ""
    system = "\n".join(
        [
            f"你正在扮演：{npc.name}。你只根据下面「你知道的事」做决定，绝不使用你不知道的信息。",
            f"人格：{npc.persona}",
            "指代规则（很重要）：",
            f"- 你自己 = {npc.name}；下文用「他/她/名字」这类第三人称提及时，指的就是你本人。",
            "- 「玩家」= 你的对话对象（人类玩家）；下文语境中的第三人称都不指你时，以「玩家」为你对话的人。",
            "- 只输出你的决定，不要输出旁白、不要替玩家做决定。",
            '只返回 JSON，格式如下：',
            '{"decision": "你的决定", "action_hint": "你会做的动作/行为", "tone": "语气"}',
            "",
            "深抉择问题：",
            question,
            memory_block,
        ]
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"剧情情境（导演旁观视角）\n{scene}"},
    ]
    data = await llm.complete_json(
        messages,
        ActorDecision,
        model=model,
        temperature=temperature,
    )
    return ActorDecision.model_validate(data)