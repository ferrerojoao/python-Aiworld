from __future__ import annotations

from app.config import DEFAULT_LIMITS, InjectionLimits
from app.core.workorder import build_work_order
from app.ledger.queries import Ledger
from app.workers.schemas import ActorDecision
from app.world.models import NpcCard, WorldContent


def clean_context(text: str) -> str:
    """只做 strip —— 绝不改写人称（2026-09-11 纠错）。

    旧实现 ``normalize_context`` 把「你/您」机械替换成「玩家」，前提假设是
    "你 = 玩家"。这个假设是错的：编剧契约要求 ``context`` 只写该 NPC 本人
    会知道的情境，编剧于是**自然地以该 NPC 为「你」**——原句「你包了一夜刚
    被刘星拍醒……是你先跟刘星提的」中「你」= 朱明、刘星 = 玩家，完全正确；
    替换成「玩家包了一夜刚被刘星拍醒」反而自相矛盾（玩家正是刘星）。
    是人称替换制造了"写反"，不是写手写错。

    人称归属改由契约约定：writer 侧约定 context 以该 NPC 为「你」、提到玩家
    一律写其**姓名**（``workorder.writer_output_format`` 的 actor_questions
    「context」字段条；2026-09-13 由二级「抉择归属」条迁入一级，字段写法贴着
    字段定义）；
    actor 侧约定「你」= 自己、「<主角名>」= 对话对象（``workorder.actor_contract``，
    主角名经 ``player_display_name`` 注入；仅当名字被写成占位符「你」时退化为「玩家」）。
    规则讲清之后，机械替换不仅多余，还会破坏本来正确的文本。
    """
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
    limits: InjectionLimits = DEFAULT_LIMITS,
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
    system = build_work_order(f"actor_{npc.id}", world, ledger, scene_id or "", limits=limits)
    scene = clean_context(context)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"深抉择问题：{question}\n你当前身处的情境（以你的处境为视角）：\n{scene}",
        },
    ]
    data = await llm.complete_json(
        messages,
        ActorDecision,
        model=model,
        temperature=temperature,
    )
    return ActorDecision.model_validate(data)