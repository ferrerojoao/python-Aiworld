from __future__ import annotations

from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import Directive, StoryOutput


async def run_storyteller(
    llm,
    world: WorldContent,
    directive: Directive,
    *,
    preset: NarrativePreset | None = None,
    player: dict | None = None,
    present_npcs: list[dict] | None = None,
    scene_name: str = "",
    scene_desc: str = "",
    lore_bodies: list[str] | None = None,
    model: str = "fake",
    temperature: float = 0.9,
) -> StoryOutput:
    """Storyteller worker: the only prose writer."""
    beats_text = "\n".join(
        f"- [{b.kind}] {b.text}" + (f"（{b.speaker}：{b.meaning}）" if b.speaker else "")
        for b in directive.beats
    )
    if directive.adopt_player_body:
        # The director decided the player's own text is already the final prose.
        return StoryOutput(prose=directive.beats[0].text if directive.beats else "")

    preset = preset or world.presets
    writing_preset = preset.storyteller_preset or f"{preset.style}\n{preset.description_style}"
    system_parts = [
        "你是 AIWorld 的说书人，是唯一正文执笔者。",
        "按叙述预设写正文：",
        writing_preset,
    ]

    # 角色资料：主角与在场 NPC 放在同一层级
    character_lines = ["角色资料："]
    if player:
        character_lines.append(
            f"[主角] 名字：{player.get('name', '')}；外貌：{player.get('appearance') or '未设定'}；人格：{player.get('persona') or '未设定'}；背景：{player.get('background') or '未设定'}"
        )
    for npc in present_npcs or []:
        character_lines.append(
            f"[{npc.get('name', '')}] 名字：{npc.get('name', '')}；外貌：{npc.get('appearance') or '未设定'}；人格：{npc.get('persona') or '未设定'}"
        )
    system_parts += character_lines

    if scene_name:
        system_parts.append("当前场景：" + scene_name)
    if scene_desc:
        system_parts.append("场景描述：" + scene_desc)
    if lore_bodies:
        system_parts.append("世界书引用：")
        system_parts.extend(lore_bodies)

    system_parts += [
        "不要写任何导演动机、幕后注。",
        "不要提到 AIWorld、系统、导演、说书人、剧本指令、玩家输入等元信息。",
        "不要打破第四面墙，只写玩家在故事里能感知到的内容。",
    ]
    system = "\n".join(system_parts)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"剧本指令：\n{beats_text}"},
    ]
    data = await llm.complete_json(
        messages,
        StoryOutput,
        model=model,
        temperature=temperature,
    )
    return StoryOutput.model_validate(data)