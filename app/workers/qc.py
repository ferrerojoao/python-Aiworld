from __future__ import annotations

from app.config import DEFAULT_LIMITS
from app.ledger.queries import Ledger
from app.workers.schemas import QCOutput, SummaryOutput
from app.world.models import NarrativePreset, WorldContent


def build_qc_reference(
    world: WorldContent,
    ledger: Ledger,
    participants: list[str],
    *,
    known_limit: int = DEFAULT_LIMITS.known_set_limit,
) -> str:
    """Reference area for the QC checker: what each speaking entity may know.

    The merged writer is omniscient, so the QC pass is the last gate against
    knowledge leaks: a character's lines must stay inside their visible set.

    ``known_limit`` 必须与编剧侧**同一份**（2026-09-16）：QC 是拿编剧的正文去
    比对知识边界的，两边条数不一致就会出现"编剧写了、QC 却以为泄漏"的假警报。
    默认值直接取 ``DEFAULT_LIMITS``，不另写数字。

    Player secrets are the opposite boundary: personal_secrets are known to
    the player only (no NPC may mention them — disguise identity), and
    private_note is known to the writer only (neither player nor NPC knows).
    Set them as forbidden zones the QC must never see leaked.
    """
    blocks = []
    scene_id = ledger.current_scene()
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    if scene:
        blocks.append(f"当前场景（{scene.id}）：{scene.perceivable}")

    player = world.player()
    if player is not None and player.personal_secrets:
        blocks.append(
            f"主角自知隐秘（只有玩家自己知道，任何 NPC 说出或提及都算泄漏，"
            f"必须脱敏：{player.personal_secrets}"
        )
    if player is not None and player.private_note:
        blocks.append(
            f"主角幕后注（连玩家也不知道的背景，只作暗示铺垫用，只有编剧心里有数："
            f"{player.private_note}"
        )

    for pid in participants or []:
        if pid == world.player_name():
            continue  # 主角的知识边界就是事件日志本身，不算"某角色的已知集"
        npc = world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.known_set(pid, scene_id, known_limit)
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
    preset: NarrativePreset | None = None,
    reference: str = "",
    summary_hint: str = "",
    model: str = "",
    worker: str = "qc",
    temperature: float = 0.2,
) -> QCOutput:
    """QC worker: style, leak and banned-words checks (all by the LLM).

    参照区**只由调用方的 ``reference`` 决定**（调用方用 ``build_qc_reference`` 算好再传）。
    这里曾有一个 ``participants`` 形参，函数体从不使用它 —— 2026-09-23 删掉：
    它诱使人以为"传 participants 就能控制参照区"，而重抽那笔正是因此长期传着空表。
    """
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
        worker=worker,
    )
    out = QCOutput.model_validate(data)
    if not out.prose:
        out.prose = prose
    if out.issues:
        out.status = "fixed"
    return out


async def sync_summary(
    llm,
    prose: str,
    *,
    preset: NarrativePreset | None = None,
    reference: str = "",
    summary_hint: str = "",
    model: str = "",
    worker: str = "qc",
    temperature: float = 0.2,
) -> str:
    """手改正文之后，判断事件日志里那句摘要要不要跟着改；返回新摘要（"" = 沿用旧的）。

    **为什么单独一笔**（2026-09-24，玩家手改候选）：候选正文被就地改掉后，摘要还是
    编剧那一稿的产物，而摘要不会自己过期——它此后每轮都被压成一行重新装配给编剧与
    导演（``workorder.event_log_block``）。陈旧摘要要么与新正文不符（后续回合照着
    废摘要往下写），要么把玩家刚删掉的私密信息继续传下去（**脱敏侧门**，与质检防的
    是同一件事）。判据因此与质检里那条 summary 字段**同一套**：不一致才改。

    **为什么不直接复用 ``run_qc``**：质检会**改写正文**（文风 / 泄漏 / 禁用词），而
    手改稿是玩家拍板的，一个字都不该再动；共用 ``QCOutput`` 只会诱使人顺手把改后的
    正文写回候选。这里只要一句摘要，输出形状也收窄成 ``SummaryOutput``。

    ``worker="qc"`` 复用"谁是辅助"的唯一名单（``config.AUX_WORKERS``）——这是同一族
    的核对活，不需要为它新开一个角色名。参照区由调用方用 ``build_qc_reference`` 算好
    传进来（与 ``run_qc`` 同一条纪律：这里不自己拼）。
    """
    system = "\n".join(
        [
            "你是 AIWorld 的摘要核对员。玩家手改了这一拍的正文，你要判断"
            "**事件日志里那句摘要**还能不能站得住——它此后会长期反复提供给编剧与导演。",
            "只返回 JSON，格式如下：",
            '{"summary": "修正后的一句话摘要（可选）"}',
            "只要命中下面任何一条，就必须给出修正版：",
            "1. 摘要说的核心事件 / 结果与手改后的正文不符。",
            "2. 手改**引入或抹去**了私密信息（参照区列出的禁区）——摘要只写参照区内的"
            "信息，它长期反复装配，写进去的隐秘会反复泄漏。",
            "3. 旧摘要为空或缺失。",
            "只有当旧摘要在事件日志里**仍然成立**（改的只是措辞、氛围、动作细节）时，"
            "才省略 summary 字段——摘要要的是稳定，不是文采。",
            "摘要写法：第三人称、事件口吻、不超过 30 字、不引用对话原文。",
        ]
    )
    messages = [{"role": "system", "content": system}]
    user_parts = [f"手改后的正文：\n{prose}"]
    if preset is not None and preset.writer_guidelines:
        user_parts.append(f"\n编剧准则（仅供理解文风，正文不必再改）：\n{preset.writer_guidelines}")
    if reference:
        user_parts.append(f"\n参照区（只读，只用于比对；其中的禁区绝不可写进摘要）：\n{reference}")
    user_parts.append(f"\n旧摘要（供一致性比对）：{summary_hint or '（无）'}")
    user_parts.append("\n请给出核对结果。")
    messages.append({"role": "user", "content": "\n".join(user_parts)})

    data = await llm.complete_json(
        messages,
        SummaryOutput,
        model=model,
        temperature=temperature,
        worker=worker,
    )
    return (SummaryOutput.model_validate(data).summary or "").strip()