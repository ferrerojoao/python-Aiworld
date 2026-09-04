from __future__ import annotations

from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import Directive, StoryOutput


async def run_storyteller(
    llm,
    world: WorldContent,
    directive: Directive,
    *,
    preset: NarrativePreset | None = None,
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
    system = "\n".join(
        [
            "你是 AIWorld 的说书人，是唯一正文执笔者。",
            "按叙述预设写正文：",
            preset.style,
            preset.description_style,
            "不要写任何导演动机、幕后注。",
            "不要提到 AIWorld、系统、导演、说书人、剧本指令、玩家输入等元信息。",
            "不要打破第四面墙，只写玩家在故事里能感知到的内容。",
        ]
    )
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