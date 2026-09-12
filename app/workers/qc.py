from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import QCOutput


def build_qc_reference(world: WorldContent, ledger: Ledger, participants: list[str]) -> str:
    """Reference area for the QC checker: what each speaking entity may know.

    The merged writer is omniscient, so the QC pass is the last gate against
    knowledge leaks: a character's lines must stay inside their visible set.

    Player secrets are the opposite boundary: personal_secrets are known to
    the player only (no NPC may mention them — disguise identity), and
    private_note is known to the writer only (neither player nor NPC knows).
    Set them as forbidden zones the QC must never see leaked.
    """
    blocks = []
    scene_id = ledger.save.player_scene
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    if scene:
        blocks.append(f"当前场景（{scene.id}）：{scene.perceivable}")

    player = ledger.save.player
    if player.personal_secrets:
        blocks.append(
            f"主角自知隐秘（只有玩家自己知道，任何 NPC 说出或提及都算泄漏，"
            f"必须脱敏：{player.personal_secrets}"
        )
    if player.private_note:
        blocks.append(
            f"主角幕后注（连玩家也不知道的背景，只作暗示铺垫用，只有编剧心里有数："
            f"{player.private_note}"
        )

    for pid in participants or []:
        npc = world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.known_set(pid, scene_id, 5)
        memo = "；".join(mem) if mem else "（无——该角色的知识从眼前开始）"
        blocks.append(
            f"{npc.id} 知道的事：{memo}（另：本区域公开旧事该角色均有耳闻；"
            f"发生在本区域之外的旧事该角色一概不知道）"
        )
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
    summary_hint: str = "",
    model: str = "fake",
    temperature: float = 0.2,
) -> QCOutput:
    """QC worker: style, leak and banned-words checks (all by the LLM)."""
    preset = preset or world.presets
    banned = list(preset.banned_words)

    system = "\n".join(
        [
            "你是 AIWorld 的质检员，负责文风、泄漏和禁用词检查。",
            "只返回 JSON，格式如下：",
            '{"status": "pass|fixed", "prose": "质检后的正文（可以是原文；改了则填改后全文）", "issues": [{"desc": "修改记录"}], "summary": "修正后的一句话摘要（可选）"}',
            "status：pass=无需修改；fixed=已修改正文。",
            "issues：修改记录列表（改了什么/为什么改），没有则为空数组。",
            "summary：仅当正文被修改且用户给的摘要与修改后正文语义不一致（尤其泄漏脱敏导致的变化）时才输出修正后的摘要；"
            "正文未动或摘要本来一致就省略此字段。摘要只写参照区内的信息——它是事件日志的长期记忆，泄漏会反复出现。",
            "具体检查项：",
            "1. 文风一致性：按下方给出的编剧准则逐条核对文风、描写方式与用词——准则里列出的每一类问题"
            "（如滥用比喻通感、强行转折、不相关对比等）都归这一项查，明显不符处局部改写为直陈其事；"
            "只动需要改的句子，其余部分保持原样透传，改动记录进 issues。",
            "2. 泄漏比对：参照区列出每个出场角色「知道的事」。某角色台词呈现的知识超出其已知范围，"
            "就是泄漏（剧情错误），必须脱敏改写为模糊表述。"
            "参照区另有两条玩家边界：主角自知隐秘只有玩家自己知道——任何 NPC 说出或提及都算泄漏；"
            "主角幕后注连玩家都不知道——正文里揭示即算泄漏（上限是暗示铺垫）。",
            "3. 禁用词：下面给出一份禁用词表。正文里出现这些词时，必须**局部改写**该处——"
            "换成等价的合规说法，句子读起来通顺、意思不变、结构完整；改写后记录到 issues。",
            "你的职责只有上面三项：文风、泄漏、禁用词；剧情走向是否合理、有无设定矛盾留给玩家判断。",
            "",
            "禁用词表：" + ("、".join(banned) if banned else "（无）"),
        ]
    )
    messages = [{"role": "system", "content": system}]
    user_parts = [f"正文：\n{prose}"]
    if preset.writer_guidelines:
        user_parts.append(f"\n编剧准则（检查项 1 的比对基准）：\n{preset.writer_guidelines}")
    if reference:
        user_parts.append(f"\n参照区（只读，只用于比对）：\n{reference}")
    if summary_hint:
        user_parts.append(f"\n用户摘要（供一致性比对，必要时在输出 summary 字段给修正版）：{summary_hint}")
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
        out.prose = prose
    if out.issues:
        out.status = "fixed"
    return out