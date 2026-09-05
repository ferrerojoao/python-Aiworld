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
    scene_id = scene_id or ledger.save.player_scene or "main_street"
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    scene_text = scene.perceivable if scene else "未知场景"
    present_ids = ledger.present_at(scene_id)
    present_names = [
        world.npcs[pid].name if pid in world.npcs else pid for pid in present_ids
    ]
    npc_names = "、".join(present_names) or "暂无"
    preset = preset or world.presets

    system_parts = [
        "你是 AIWorld 的导演，只负责排戏的走向，不直接写正文。",
        "必须输出至少一个 narrate 或 speech 节拍，不要输出空的 beats。",
        "世界概要（硬规则，不可违背）：",
        *world.meta.summary,
    ]
    player = ledger.save.player
    system_parts += [
        "玩家角色：",
        f"名字：{player.name}",
        f"外貌：{player.appearance or '未设定'}",
        f"人格：{player.persona or '未设定'}",
        f"背景：{player.background or '未设定'}",
    ]
    if preset.director_guidelines:
        system_parts.append("导演准则：")
        system_parts.append(preset.director_guidelines)
    else:
        system_parts.append("叙述预设：")
        system_parts.append(preset.style)
        system_parts.append(preset.description_style)
    system_parts += [
        "在场 NPC：" + npc_names,
        "当前场景：" + (scene.name if scene else "主街"),
        scene_text,
        "",
        "输出示例（必须包含至少一个 beats 元素）：",
        '{"mode":"scene","beats":[{"kind":"narrate","text":"玩家走进网吧，朱明抬头看他。"},{"kind":"speech","speaker":"npc_zhuming","meaning":"不想提打架的事","tone_hint":"敷衍"}],"lore_refs":[],"motivation_note":"","adopt_player_body":false,"private":false,"location":"net_bar","participants":["player","npc_zhuming"]}',
    ]
    system = "\n".join(system_parts)
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