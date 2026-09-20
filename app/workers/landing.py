"""落卡草稿：从**已采纳正文**里总结出人物 / 场景的最小字段（落卡窗口 2.0）。

**这是教义变更**（2026-09-19 用户拍板）。旧口径是"起草只做机械提取，拟稿由玩家
完成，引擎不代笔"，理由是"编出来的内容会伪装成事实"。现在允许 LLM 起草——**风险
没变**，所以防线换掉，三条缺一不可：

1. **输入只有证据**：只喂该主体自己的原文事件（人物走 ``by_participant``、场景走
   ``by_location``）。不喂世界书、不喂别人的卡、不喂底牌（``private_note`` /
   ``personal_secrets``）——断掉"从别处推出来"的路径。本模块的
   ``evidence_of()`` 同时供落卡窗口展示用，所以"草稿只喂了你看得见的那几条"
   是**同一份数据**，玩家可以逐句对账。
2. **硬性"不许超出"**：写不出就留空；空栏是"还没写到"，不是"没有"。
3. **草稿 ≠ 事实**：本模块只**返回**草稿。落盘与否由玩家在窗口拍板，且调用方
   必须把它标成「AI 草稿」并与原文证据并排展示。

只保留**可从证据推导**的字段（这正是"最小需求"的硬理由，不只是省事）：
外貌 / 人格 / 场景描述能总结；``private_note`` / ``personal_secrets`` 定义上就是
"无人知道的真相"、正文根本没写 → **不可能被总结**，所以不在草稿里，落卡后到
工作台填。
"""

from __future__ import annotations

from pydantic import BaseModel

# 取最近几条正文当证据。与落卡窗口展示的上限**共用这一个数字**——两边不一致的话
# "草稿只喂了你看得见的那几条"就不成立了。
EVIDENCE_LIMIT = 8


class NpcLandingDraft(BaseModel):
    appearance: str = ""
    persona: str = ""


class SceneLandingDraft(BaseModel):
    perceivable: str = ""


def evidence_of(events: list[dict]) -> list[dict]:
    """从索引里取落卡证据（最近 ``EVIDENCE_LIMIT`` 条，含正文或摘要）。

    索引（``by_participant`` / ``by_location``）只收 ``kind == "narrative"`` 的
    事件，所以这里天然看不到记账 / 状态事件。占位事件（如 ``npc_moves`` 那条
    ``body=""`` 的轻量位置事件）只有摘要行，仍算"已写出的事实"——一并给出去，
    这样"草稿只喂了窗口里看得见的那些"就是字面为真。
    """
    picked = [
        ev
        for ev in events
        if (ev.get("body") or "").strip() or (ev.get("summary") or "").strip()
    ][-EVIDENCE_LIMIT:]
    return [
        {
            "id": ev["id"],
            "at": ev.get("at", ""),
            "location": ev.get("location") or "",
            "summary": ev.get("summary", ""),
            "body": ev.get("body", ""),
        }
        for ev in picked
    ]


def _evidence_block(evidence: list[dict]) -> str:
    lines: list[str] = []
    for i, ev in enumerate(evidence, 1):
        lines.append(f"[{i}] {ev.get('at') or '-'} · {ev.get('location') or '-'}")
        lines.append((ev.get("body") or ev.get("summary") or "").strip())
    return "\n".join(lines)


_NPC_SYSTEM = """你只做一件事：把「已采纳正文」里关于某个角色的原文，**总结**成人物卡的两个字段。

只输出 JSON：{"appearance": "...", "persona": "..."}

- appearance（外貌）：只写**外表看得见的**——体态、衣着、面相、习惯性动作。
  不写身份、不写性格。
- persona（人格）：只写正文里**演出来过**的性格与说话方式（他怎么对人、在意什么、
  怕什么、讲话什么调子）。不写身份关系以外的推测。

硬性纪律（违反即废）：

- **只总结下面给出的正文里已经写出的东西。没写的字段就留空串。**
  「留空」是「还没写到」，不是「没有」——不要替他补。
- **不许推断、不许润色、不许补前史 / 来历 / 人际 / 动机 / 隐藏设定。**
  给出的正文里没有的，一律不写进结果。
- 每个字段一到两句，各不超过 60 字。
- 正文里出现与这个角色无关的别人，不要把别人的事写进他的字段。
- 只输出 JSON 对象本身。"""


_SCENE_SYSTEM = """你只做一件事：把「已采纳正文」里关于某个地点的原文，**总结**成这个场景
「一眼能感知到」的描述。

只输出 JSON：{"perceivable": "..."}

- perceivable（可感知区）= **一眼能感知到的东西**：形制、大小、光线、气味、声音、
  陈设、材质、天气。引擎把它**直接拼进场景描述**、每轮发给编剧当环境设定，所以
  它本身要是一段完整、能独立成句的话，以句号收尾。
- **绝对不写**：历史事件、谁来过、发生过什么、这里有什么意义、任何推断或背景知识。
  ✗「这里曾是主角与卢克交手的地方」
  ✓「三层废弃砖楼，扶手断了大半，墙上有霉斑，风从破窗灌进来」

硬性纪律（违反即废）：

- **只总结下面给出的正文里已经写出的东西**；正文里没写到的感知细节不要补。
- 一到两句，不超过 60 字，以句号收尾。
- 只输出 JSON 对象本身。"""


async def draft_npc_card(
    llm,
    ledger,
    name: str,
    *,
    model: str = "",
    worker: str = "landing",
    temperature: float = 0.3,
) -> dict:
    """总结一个未落卡人物的草稿。没有证据 → 不调 LLM，直接给空字段。"""
    evidence = evidence_of(ledger.by_participant.get(name, []))
    if not evidence:
        return {"appearance": "", "persona": "", "evidence_count": 0}
    messages = [
        {"role": "system", "content": _NPC_SYSTEM},
        {
            "role": "user",
            "content": f"角色：{name}\n\n已采纳正文里与他相关的原文（从早到晚）：\n"
            + _evidence_block(evidence),
        },
    ]
    data = await llm.complete_json(
        messages, NpcLandingDraft, model=model, temperature=temperature, worker=worker
    )
    draft = NpcLandingDraft.model_validate(data)
    return {
        "appearance": draft.appearance.strip(),
        "persona": draft.persona.strip(),
        "evidence_count": len(evidence),
    }


async def draft_scene(
    llm,
    ledger,
    name: str,
    *,
    model: str = "",
    worker: str = "landing",
    temperature: float = 0.3,
) -> dict:
    """总结一个待落卡场景的草稿。没有证据 → 不调 LLM，直接给空字段。"""
    evidence = evidence_of(ledger.by_location.get(name, []))
    if not evidence:
        return {"perceivable": "", "evidence_count": 0}
    messages = [
        {"role": "system", "content": _SCENE_SYSTEM},
        {
            "role": "user",
            "content": f"地点：{name}\n\n已采纳正文里发生在这里的原文（从早到晚）：\n"
            + _evidence_block(evidence),
        },
    ]
    data = await llm.complete_json(
        messages, SceneLandingDraft, model=model, temperature=temperature, worker=worker
    )
    draft = SceneLandingDraft.model_validate(data)
    return {"perceivable": draft.perceivable.strip(), "evidence_count": len(evidence)}
