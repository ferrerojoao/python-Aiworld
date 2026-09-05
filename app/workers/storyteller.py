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
    if player:
        system_parts += [
            "玩家角色：",
            f"名字：{player.get('name', '')}",
            f"外貌：{player.get('appearance') or '未设定'}",
            f"人格：{player.get('persona') or '未设定'}",
            f"背景：{player.get('background') or '未设定'}",
        ]
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