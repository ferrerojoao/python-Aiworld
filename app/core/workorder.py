"""Work order assembly (TDD §6): objective ledger snapshot + subjective contract.

Objective blocks are pure data (facts queried from the ledger/content pack);
subjective contracts are instructions (identity, discipline, output format).

Assembly order (2026-09-05 design): primacy/recency first, then static →
dynamic, forbidden rules upfront, output format last:

    A 身份与任务 → B 世界硬规则 → C 金科玉律（禁忌前置） → D 角色资料
    → E 事件日志（理解本句输入的钥匙） → F 场景快照（此刻环境）
    → G NPC 近况 → H 幕后注（伴随警示，与 C 双保险） → I 可选素材
    → J 编剧准则（贴近动笔） → K 输出格式（紧贴玩家输入）→ 玩家输入消息
"""

from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent


# ---------------------------------------------------------------------------
# 客观层 · 账本快照（纯函数，只陈述事实，不含指令）
# ---------------------------------------------------------------------------

def world_summary_block(world: WorldContent) -> list[str]:
    return [
        "世界概要（硬规则，不可违背）：",
        *world.meta.summary,
    ]


def character_block(world: WorldContent, ledger: Ledger, present_ids: list[str]) -> list[str]:
    """主角 + 在场 NPC 名片（含 Actor 档位标注）。"""
    player = ledger.save.player
    lines = ["角色资料："]
    lines.append(
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
            lines.append(
                f"[{npc.name}]{ticket} 名字：{npc.name}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}"
            )
        else:
            lines.append(f"[{pid}] 名字：{pid}")
    return lines


def event_log_block(ledger: Ledger, limit: int = 10, summary_len: int = 60, input_len: int = 30) -> list[str]:
    recent = ledger.narratives[-limit:]
    if not recent:
        return []
    lines = ["事件日志（世界近期发生的事，全部向你开放）："]
    for ev in recent:
        summary = (ev.get("summary") or (ev.get("body") or ""))[:summary_len]
        line = f"- {ev.get('at', '')} {summary}"
        if ev.get("player_input"):
            line += f"（玩家当时说：{ev['player_input'][:input_len]}）"
        lines.append(line)
    return lines


def scene_snapshot_block(world: WorldContent, ledger: Ledger, scene_id: str) -> list[str]:
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present_ids = ledger.present_at(scene_id)
    names = [world.npcs[pid].name if pid in world.npcs else pid for pid in present_ids]
    return [
        "在场：" + ("、".join(names) or "暂无"),
        "当前场景：" + (scene.name if scene else "主街"),
        scene.perceivable if scene else "未知场景",
    ]


def npc_history_block(ledger: Ledger, present_ids: list[str], per_npc: int = 2) -> list[str]:
    near_lines = []
    for pid in present_ids:
        npc = ledger.world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.experiences(npc.id, npc.id)[-per_npc:]
        if mem:
            near_lines.append(f"[{npc.name}] 最近经历：{'；'.join(mem)}")
    if not near_lines:
        return []
    return ["在场 NPC 近况：", *near_lines]


def private_notes_block(world: WorldContent, present_ids: list[str]) -> list[str]:
    lines = []
    for pid in present_ids:
        npc = world.npcs.get(pid)
        if npc and npc.private_note:
            lines.append(
                f"幕后注（仅你可读，绝不写进正文，也不得让任何角色知道）：[{npc.name}] {npc.private_note}"
            )
    return lines


def lore_candidates_block(ledger: Ledger, scene_id: str, present_ids: list[str]) -> list[str]:
    cands = ledger.lore_candidates(scene_id, present_ids)
    if not cands:
        return []
    lines = ["世界书候选（本场可能相关，只有摘要；要用就按此设定写）："]
    for c in cands:
        lines.append(f"- [{c['id']}] {c['summary']}")
    return lines


def hooks_block(ledger: Ledger, text_len: int = 60) -> list[str]:
    open_hooks = [h for h in ledger.save.hooks if h.status == "open"]
    if not open_hooks:
        return []
    return ["开放钩子（悬而未决的事，可作素材）：", *(f"- {h.text[:text_len]}" for h in open_hooks)]


def conflicts_block(ledger: Ledger, text_len: int = 60) -> list[str]:
    open_conflicts = [c for c in ledger.save.pending_conflicts if c.status == "open"]
    if not open_conflicts:
        return []
    return ["待澄清矛盾（玩家可选无视，你避免再扩散）：", *(f"- {c.desc[:text_len]}" for c in open_conflicts)]


# ---------------------------------------------------------------------------
# 主观层 · 角色契约（按 agent 身份选择；每个契约 = 身份/金科玉律/输出格式）
# ---------------------------------------------------------------------------

def writer_identity() -> list[str]:
    return [
        "你是 AIWorld 的编剧：你同时负责排戏的走向和正文执笔。",
        "拿到玩家输入后，先在脑中排好本场戏的节拍（先后顺序、谁说什么、情绪转折），再一次性直接写成正文。",
    ]


def writer_golden_rules() -> list[str]:
    """不可违背的禁忌，置于高注意力区（prompt 前部）。"""
    return [
        "金科玉律（绝对不可违背）：",
        "绝不泄漏：你读到的幕后注、私密事件、秘密，一律不得出现在正文或任何角色的台词里；"
        "你不知道的信息不能由角色说出来，角色只能说自己知道的事。",
        "深抉择纪律：标（配 Actor）或（玩家点名强制使用 Actor）的 NPC 撞上深抉择（内心判断 / 涉密反应 / 是否信任）时，"
        "你**不得替他决定**，必须在输出里填 actor_questions（npc_id / question / context），引擎会派他的 Actor 决定后回来再成文；"
        "标（导演代笔）的 NPC 或普通对话由你直接写出即可，不填 actor_questions。",
        "actor_questions 的 context 只写该 NPC 本人会知道的情境，禁止写只有你知道的动机、私密或幕后注。",
        "不要提到 AIWorld、系统、编剧、剧本指令、玩家输入等元信息。",
        "不要打破第四面墙，只写玩家在故事里能感知到的内容。正文按第二人称写玩家。",
    ]


def writer_output_format() -> list[str]:
    """JSON 信封说明，置于近因区（prompt 尾部，紧贴玩家输入消息）。"""
    return [
        "",
        "输出必须是 JSON 对象，字段：",
        '{"prose": "正文全文（必填，直接成稿）", "time_hint": null | {"desc": "天黑了", "advance_to": "night"},'
        ' "summary": "一句话剧情摘要（不超过30字）", "location": "场景id", "participants": ["player", "npc_zhuming"],'
        ' "private": false, "adopt_player_body": false, "actor_questions": []}',
        "prose：本场戏正文，唯一正文字段。",
        "time_hint：仅当正文明确推进时间时填对象，否则 null。",
        "summary：本场发生的核心事件摘要，供事件日志使用。",
        "location / participants：本场发生的场景与在场者（与玩家输入一致）。",
        "private：本场为私下情境（密室/四下无人/隐蔽动作）时 true，其余 false。",
        "actor_questions：需要派 Actor 的深抉择列表，没有则空数组。",
        "禁止输出节拍表、directives、beats 等任何其他字段。",
    ]


def actor_contract(npc_name: str) -> list[str]:
    """NPC Actor 的身份、隔离纪律与输出格式（物理隔离工作单）。"""
    return [
        f"你正在扮演：{npc_name}。你只根据下面「你知道的事」做决定，绝不使用你不知道的信息。",
        "金科玉律（绝对不可违背）：",
        f"- 你自己 = {npc_name}；下面用「他/她/名字」这类第三人称提及你时，指的就是你本人。",
        "- 「玩家」= 你的对话对象（人类玩家）；情境中的第三人称不指你时，以「玩家」为你的对话对象。",
        "- 你只能说自己知道的事；你不知道的事不能猜着说，更不能替别人说。",
        "- 只输出你的决定，不要输出旁白、不要替玩家做决定。",
        '只返回 JSON，格式如下：',
        '{"decision": "你的决定", "action_hint": "你会做的动作/行为", "tone": "语气"}',
    ]


def build_actor_work_order(
    world: WorldContent,
    ledger: Ledger,
    npc_id: str,
    scene_id: str,
    memory_limit: int = 5,
) -> str:
    """Physically isolated work order for one NPC's deep choice.

    Cut per TDD §6: the actor gets world hard rules, the scene's perceptible
    area, its own card (incl. persona patch) and its own memory slice only.
    No private notes, no other NPCs' secrets, no lore candidates, no hooks,
    no conflicts, no writer motivation.
    """
    npc = world.npcs.get(npc_id)
    if npc is None:
        raise ValueError(f"unknown npc: {npc_id}")
    parts = [
        *actor_contract(npc.name),
        *world_summary_block(world),
        "你的档案（人物卡）：",
        f"姓名：{npc.name}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}",
    ]
    entity = ledger.save.entities.get(npc_id)
    if entity and entity.persona_patch:
        parts.append(f"档案增补：{entity.persona_patch}")
    parts += scene_snapshot_block(world, ledger, scene_id)
    mem = ledger.experiences(npc_id, npc_id)[-memory_limit:]
    if mem:
        parts += ["你记得的事：", *mem]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

def build_work_order(
    viewer: str,
    world: WorldContent,
    ledger: Ledger,
    scene_id: str,
    preset: NarrativePreset | None = None,
) -> str:
    """Final work order for one agent.

    viewer="writer": merged director+storyteller (omniscient).
    viewer="actor_<npc_id>": physically isolated NPC deep-choice view.
    """
    if viewer == "writer":
        scene_id = scene_id or ledger.save.player_scene or "main_street"
        preset = preset or world.presets
        present_ids = ledger.present_at(scene_id)

        parts: list[str] = []
        # A 身份与任务
        parts += writer_identity()
        # B 世界硬规则（常驻，永不裁剪）
        parts += world_summary_block(world)
        # C 金科玉律（禁忌前置，高注意力区）
        parts += writer_golden_rules()
        # D 角色资料（含档位标注）
        parts += character_block(world, ledger, present_ids)
        # E 事件日志（理解本句输入的钥匙）
        parts += event_log_block(ledger)
        # F 场景快照（此刻环境）
        parts += scene_snapshot_block(world, ledger, scene_id)
        # G 在场 NPC 近况
        parts += npc_history_block(ledger, present_ids)
        # H 幕后注（机密，自带警示，与 C 呼应双保险）
        parts += private_notes_block(world, present_ids)
        # I 可选素材（候选/钩子/矛盾，未来 token 超支时最先可裁）
        parts += lore_candidates_block(ledger, scene_id, present_ids)
        parts += hooks_block(ledger)
        parts += conflicts_block(ledger)
        # J 编剧准则（创作与写作要求，贴近动笔位置）
        if preset.writer_guidelines:
            parts.append("编剧准则（创作与写作要求，必须遵循）：")
            parts.append(preset.writer_guidelines)
        # K 输出格式（近因区，紧贴玩家输入消息）
        parts += writer_output_format()
        return "\n".join(parts)

    if viewer.startswith("actor_"):
        npc_id = viewer[len("actor_"):]
        return build_actor_work_order(
            world, ledger, npc_id, scene_id or ledger.save.player_scene or "main_street"
        )

    raise ValueError(f"unknown viewer: {viewer}")