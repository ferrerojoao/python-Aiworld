from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import Directive


async def run_director(
    llm,
    world: WorldContent,
    ledger: Ledger,
    player_input: str,
    *,
    rule_bundle: dict | None = None,
    scene_id: str | None = None,
    preset: NarrativePreset | None = None,
    model: str = "fake",
    temperature: float = 0.7,
) -> Directive:
    """Director worker: decides the shape of a turn."""
    scene = next((s for s in world.scenes if s.id == (scene_id or "main_street")), None)
    scene_text = scene.perceivable if scene else "未知场景"
    npc_names = "、".join(npc.name for npc in world.npcs.values()) or "暂无"
    preset = preset or world.presets

    system = "\n".join(
        [
            "你是 AIWorld 的导演，只负责排戏的走向，不直接写正文。",
            "世界概要（硬规则，不可违背）：",
            *world.meta.summary,
            "叙述预设：",
            preset.style,
            preset.description_style,
            "在场 NPC：" + npc_names,
            "当前场景：" + (scene.name if scene else "主街"),
            scene_text,
        ]
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"玩家输入：{player_input}"},
    ]
    if rule_bundle:
        messages.append({"role": "user", "content": f"规则段预结算：{rule_bundle}"})

    data = await llm.complete_json(
        messages,
        Directive,
        model=model,
        temperature=temperature,
    )
    return Directive.model_validate(data)