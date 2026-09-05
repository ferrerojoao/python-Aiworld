from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import QCOutput


def build_qc_reference(world: WorldContent, ledger: Ledger, participants: list[str]) -> str:
    """Reference area for the QC checker: what each speaking entity may know.

    The merged writer is omniscient, so the QC pass is the last gate against
    knowledge leaks: a character's lines must stay inside their visible set.
    """
    blocks = []
    scene_id = ledger.save.player_scene
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    if scene:
        blocks.append(f"当前场景（{scene.name}）：{scene.perceivable}")
    for pid in participants or []:
        npc = world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.experiences(npc.id, npc.id)[-5:]
        memo = "；".join(mem) if mem else "（无）"
        blocks.append(f"{npc.name}（{npc.id}）知道的事：{memo}")
    return "\n".join(blocks)


async def run_qc(
    llm,
    world: WorldContent,
    ledger: Ledger,
    prose: str,
    *,
    participants: list[str] | None = None,
    preset: NarrativePreset | None = None,
    reference: str = "",
    model: str = "fake",
    temperature: float = 0.2,
) -> QCOutput:
    """QC worker: style check, leak check, banned words, conflicts."""
    preset = preset or world.presets
    banned = set(preset.banned_words)
    issues: list[dict] = []
    fixed = prose

    for word in banned:
        if word in fixed:
            fixed = fixed.replace(word, "……")
            issues.append({"level": "minor", "desc": f"禁用词替换: {word}"})

    system = "\n".join(
        [
            "你是 AIWorld 的质检员，负责文风、泄漏和禁用词检查。",
            "只返回 JSON，格式如下：",
            '{"status": "pass|fixed|conflict", "prose": "质检后的正文（可修改，不得为空）", "issues": [{"level": "minor|major", "desc": "问题描述"}]}',
            "status：pass=无需修改；fixed=已修改正文或替换禁用词；conflict=发现大矛盾（正文照常给出，矛盾只挂起不阻塞）。",
            "issues：发现的问题列表，没有则为空数组。",
            "泄漏比对（必须执行）：参照区列出每个出场角色「知道的事」。某角色台词呈现的知识超出其已知范围，"
            "就是泄漏（剧情错误），必须脱敏改写为模糊表述。玩家自己的台词不受限。",
        ]
    )
    messages = [{"role": "system", "content": system}]
    user_parts = [f"正文：\n{fixed}"]
    if reference:
        user_parts.append(f"\n参照区（只读比对用，禁止出现在正文里）：\n{reference}")
    user_parts.append("\n请给出质检结果。")
    messages.append({"role": "user", "content": "\n".join(user_parts)})

    data = await llm.complete_json(
        messages,
        QCOutput,
        model=model,
        temperature=temperature,
    )
    out = QCOutput.model_validate(data)
    if not out.prose:
        out.prose = fixed
    if issues:
        out.issues.extend(issues)
        out.status = "fixed"
    return out