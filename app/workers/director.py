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

    # 角色资料：主角与在场 NPC 放在同一层级
    player = ledger.save.player
    character_lines = ["角色资料："]
    character_lines.append(
        f"[主角] 名字：{player.name}；外貌：{player.appearance or '未设定'}；人格：{player.persona or '未设定'}；背景：{player.background or '未设定'}"
    )
    for pid in present_ids:
        npc = world.npcs.get(pid)
        if npc:
            entity = ledger.save.entities.get(pid)
            ticket = ""
            if entity and entity.forced_actor:
                ticket = "（玩家点名强制使用 Actor）"
            elif npc.has_actor:
                ticket = "（配 Actor）"
            else:
                ticket = "（导演代笔）"
            character_lines.append(
                f"[{npc.name}]{ticket} 名字：{npc.name}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}"
            )
        else:
            character_lines.append(f"[{pid}] 名字：{pid}")
    system_parts += character_lines
    system_parts += [
        "档位纪律：",
        "标（配 Actor）或（玩家点名强制使用 Actor）的 NPC 撞上深抉择（内心判断 / 涉密反应 / 是否信任）时，"
        "必须输出 kind=actor 节点（actor_npc_id=该 NPC 的 id，text=抉择问题）。",
        "标（导演代笔）的 NPC 一律由你直接排戏，不要输出 actor 节点。",
        "actor 节点的 meaning 只写该 NPC 本人会知道的情境，禁止写只有导演/作者知道的动机与秘密。",
    ]

    if preset.director_guidelines:
        system_parts.append("导演准则：")
        system_parts.append(preset.director_guidelines)
    else:
        system_parts.append("叙述预设：")
        system_parts.append(preset.style)
        system_parts.append(preset.description_style)
    system_parts += [
        "在场：" + npc_names,
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