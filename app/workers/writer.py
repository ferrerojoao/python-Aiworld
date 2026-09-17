from __future__ import annotations

from app.config import DEFAULT_LIMITS, InjectionLimits
from app.core.workorder import build_work_order
from app.ledger.queries import Ledger
from app.workers.schemas import WriterOutput
from app.world.models import NarrativePreset, WorldContent


def _rule_brief(bundle: dict) -> str:
    """规则段预结算说人话。

    规则侧现在只剩**移动**一件事（跳时解析已于 2026-09-16 整条下线）：时间由
    审计在采纳时从正文推断，编剧不需要任何"时钟将被定到几点"的预告。

    历史（2026-09-14）：原先把整个 dict 原样 repr 进 prompt（``规则段预结算：
    {'route': 'jump', 'delta_minutes': 720}``）——编剧只能靠猜字段名，而且
    route/delta 这类引擎内部词汇对它毫无意义。
    """
    lines: list[str] = []
    destination = bundle.get("destination")
    if destination:
        lines.append(f"- 主角这一拍确定移动到：{destination}（正文从离开当前场景写起）")
    if not lines:
        return ""
    return (
        "规则段预结算（引擎已按玩家输入算好的硬事实，与你的叙述冲突时以它为准）：\n"
        + "\n".join(lines)
    )


async def run_writer(
    llm,
    world: WorldContent,
    ledger: Ledger,
    player_input: str,
    *,
    rule_bundle: dict | None = None,
    scene_id: str | None = None,
    preset: NarrativePreset | None = None,
    prior_output: WriterOutput | None = None,
    actor_decisions: str = "",
    own_decisions: str = "",
    rewrite_note: str = "",
    writer_directive: str = "",
    limits: InjectionLimits = DEFAULT_LIMITS,
    model: str = "fake",
    temperature: float = 0.8,
) -> WriterOutput:
    """Single-agent writer: decides the scene and writes the prose in one call.

    The work order (objective snapshot + subjective contract) is assembled by
    ``app.core.workorder.build_work_order``.

    深抉择两段式（2026-09-11 修复）：编剧上缴 = 它在那一拍**停笔**，所以只要有
    上缴就一定有第二稿。第二稿的锚点由两部分组成——

    - ``prior_output``：一稿以 **assistant 消息**回填（「你上一条回答」的形状，
      与 ``llm.complete_json`` 的重试反馈同构），``actor_questions`` 清空——那条
      问题已被处理，留着会诱导二稿再上缴一遍。此前二稿只收到一句「已由…决定」
      （48 字），一稿正文 100% 丢弃，模型只能凭空重排，于是只写后半段。
    - ``actor_decisions``：派了 Actor 的角色，按本人决定重写。
    - ``own_decisions``：没派 Actor 的（无票 / 不在场 / npc_id 无法识别），明说
      「必须由你直接拍板」——这是第二稿能补全残稿的关键（2026-09-12 去否定化：
      原句尾还挂着一句"不要把这一拍留空"，已删——标题本身已把要求说清）。
    """
    system = build_work_order("writer", world, ledger, scene_id or "", preset=preset, limits=limits)
    if player_input.strip():
        player_msg = {"role": "user", "content": f"玩家输入：{player_input}"}
    else:
        player_msg = {
            "role": "user",
            "content": "玩家输入：（本轮无行动，按导演要求与当前情境自然推进）",
        }
    messages = [
        {"role": "system", "content": system},
        player_msg,
    ]
    if writer_directive:
        messages.append(
            {
                "role": "user",
                "content": "本回合导演要求（必须执行，但不写进正文、不算玩家台词、不进事件日志）：\n"
                + writer_directive,
            }
        )
    if rule_bundle:
        brief = _rule_brief(rule_bundle)
        if brief:
            messages.append({"role": "user", "content": brief})
    if prior_output is not None:
        echo = prior_output.model_copy(update={"actor_questions": []})
        messages.append({"role": "assistant", "content": echo.model_dump_json()})
    if actor_decisions or own_decisions:
        blocks = []
        if actor_decisions:
            blocks.append("【必须按本人决定重写的部分】\n" + actor_decisions)
        if own_decisions:
            blocks.append(
                "【本轮不派 Actor、必须由你直接拍板的部分】\n" + own_decisions
            )
        blocks.append(
            "输出整场正文全文（从第一句话开始）；"
            "summary 覆盖整场——上一稿摘要与新增部分合在一起。"
        )
        messages.append({"role": "user", "content": "\n".join(blocks)})
    if rewrite_note:
        messages.append({"role": "user", "content": f"改写要求：{rewrite_note}"})

    data = await llm.complete_json(
        messages,
        WriterOutput,
        model=model,
        temperature=temperature,
    )
    return WriterOutput.model_validate(data)