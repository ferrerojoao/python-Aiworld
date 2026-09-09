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

import datetime as dt

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


def character_block(world: WorldContent, ledger: Ledger, present_ids: list[str], include_ids: bool = False) -> list[str]:
    """玩家资料 + 在场 NPC 名片（含 Actor 档位标注，可选附带 id）。

    玩家与 NPC 分两段，避免「角色」笼统概念混用。主角与 NPC 字段一致：
    名字/外貌/人格 + 幕后注/自知隐秘（见 private_notes_block）。
    """
    player = ledger.save.player
    lines = ["玩家资料："]
    lines.append(
        f"[主角] 名字：{player.name}；外貌：{player.appearance or '未设定'}；人格：{player.persona or '未设定'}"
    )
    npc_lines = []
    for pid in present_ids:
        npc = world.npcs.get(pid)
        if npc:
            ticket = "（配 Actor）" if npc.has_actor else "（导演代笔）"
            id_tag = f"（id: {npc.id}）" if include_ids else ""
            npc_lines.append(
                f"[{npc.name}]{id_tag}{ticket} 名字：{npc.name}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}"
            )
        else:
            npc_lines.append(f"[{pid}] 名字：{pid}")
    if npc_lines:
        lines.append("在场 NPC：")
        lines.extend(npc_lines)
    return lines


def event_log_block(world: WorldContent, ledger: Ledger, limit: int = 10, recent_full: int = 3, summary_len: int = 60, input_len: int = 30, include_ids: bool = False) -> list[str]:
    """Event log for the writer: older ones as summaries, the newest 3 as
    full prose (so the writer can "continue" straight after them).

    每行标注发生地、在场者与时间——写手据此执行「角色不是你」知情总纲
    （公开事件 ≠ 人人皆知、异地不知、即兴角色只知眼前）。
    """

    def _id_tag(ev: dict) -> str:
        return f"[{ev['id']}] " if include_ids and ev.get("id") else ""

    def _where(ev: dict) -> str:
        loc = ev.get("location") or ""
        if not loc:
            return ""
        scene = next((s for s in world.scenes if s.id == loc), None)
        return scene.name if scene else (ev.get("location_name") or loc)

    def _ctx_tag(ev: dict) -> str:
        parts = []
        where = _where(ev)
        if where:
            parts.append(where)
        pids = ev.get("participants", [])
        if pids:
            names = [
                ledger.save.player.name if pid == "player"
                else (world.npcs[pid].name if pid in world.npcs else pid)
                for pid in pids
            ]
            parts.append("在场者：" + "、".join(names))
        return f"（{' · '.join(parts)}）" if parts else ""

    def _summary_line(ev: dict) -> str:
        summary = (ev.get("summary") or (ev.get("body") or ""))[:summary_len]
        at = ev.get("at", "")
        pinput = ev.get("player_input")
        if pinput:
            line = f"- {_id_tag(ev)}{at} {_ctx_tag(ev)} 玩家：「{pinput[:input_len]}」 {summary}"
        else:
            line = f"- {_id_tag(ev)}{at} {_ctx_tag(ev)} {summary}"
        return line

    recent = ledger.narratives[-limit:]
    if not recent:
        return []
    lines = ["事件日志（世界近期发生的事）："]
    older = recent[:-recent_full] if len(recent) > recent_full else []
    if older:
        lines.append("更早事件（摘要）：")
        lines.extend(_summary_line(ev) for ev in older)
    newest = recent[-recent_full:]
    lines.append("最近剧情（原文）：")
    for ev in newest:
        lines.append(f"- {_id_tag(ev)}{ev.get('at', '')} {_ctx_tag(ev)}：{ev.get('body', '')}")
    return lines


def _rel_seen(clock: str, at: str) -> str:
    """快照新鲜度：最后目击时刻相对当前时钟的人类表述；任一侧解析失败返回空。"""
    try:
        now = dt.datetime.fromisoformat(clock)
        seen = dt.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return ""
    minutes = int((now - seen).total_seconds() // 60)
    if minutes < 60:
        return "刚刚" if minutes < 10 else f"{minutes} 分钟前"
    if minutes < 1440:
        return f"{minutes // 60} 小时前"
    return f"{minutes // 1440} 天前"


def scene_snapshot_block(world: WorldContent, ledger: Ledger, scene_id: str) -> list[str]:
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present_ids = ledger.present_at(scene_id)
    clock = ledger.save.clock or ""
    parts = []
    for pid in present_ids:
        name = world.npcs[pid].name if pid in world.npcs else pid
        ev = ledger.where_is(pid)
        seen = _rel_seen(clock, ev.get("at", "")) if ev else ""
        parts.append(f"{name}（最后目击：{seen}）" if seen else name)
    return [
        "当前时间：" + (clock or "-"),
        "在场（括号内 = 该角色最后被记录在此的时刻，久未见面的要考虑他是否还在）："
        + ("、".join(parts) or "暂无"),
        "当前场景：" + (scene.name if scene else "主街"),
        scene.perceivable if scene else "未知场景",
    ]


def known_set_block(world: WorldContent, ledger: Ledger, present_ids: list[str], scene_id: str, per_npc: int = 5) -> list[str]:
    """在场 NPC 已知集：机械计算的知识边界（亲历 ∪ known_by ∪ 同区域公开）。

    取代旧"近况块"——写手与 QC 共用同一份清单，单一事实源。
    """
    lines = []
    for pid in present_ids:
        npc = ledger.world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.known_set(pid, scene_id, per_npc)
        if mem:
            lines.append(f"[{npc.name}] 知道的事：{'；'.join(mem)}")
        else:
            lines.append(f"[{npc.name}] 知道的事：（无——该角色的知识从眼前开始）")
    if not lines:
        return []
    return [
        "在场 NPC 已知集（每个角色的知识边界，表述以其为准；清单外的事件这些角色一律不知道，"
        "包括事件日志里发生在别处的事）：",
        *lines,
    ]


def private_notes_block(world: WorldContent, ledger: Ledger, present_ids: list[str]) -> list[str]:
    """幕后注：作者底牌与角色自知隐秘（主角同 NPC，字段一致）。

    消费方：编剧/导演（作者侧全知）。永不进任何 Actor 切片与玩家视角。
    """
    lines = []
    player = ledger.save.player
    if player.private_note or player.personal_secrets:
        notes = []
        if player.private_note:
            notes.append(player.private_note)
        if player.personal_secrets:
            notes.append(f"[{player.name} 自知] {player.personal_secrets}")
        lines.append(
            f"幕后注（仅你可读，绝不写进正文，也不得让任何角色知道）：[主角·{player.name}] {'；'.join(notes)}"
        )
    for pid in present_ids:
        npc = world.npcs.get(pid)
        if npc and (npc.private_note or npc.personal_secrets):
            notes = []
            if npc.private_note:
                notes.append(npc.private_note)
            if npc.personal_secrets:
                notes.append(f"[{npc.name} 自知] {npc.personal_secrets}")
            lines.append(
                f"幕后注（仅你可读，绝不写进正文，也不得让任何角色知道）：[{npc.name}] {'；'.join(notes)}"
            )
    return lines


def active_lore_block(ledger: Ledger) -> list[str]:
    """World book entries triggered for this turn (save.active_lore_ids).

    The engine maintains the active list (keyword hits on player input at
    pre-solve; rebuild from adopted prose at commit); this block only renders
    the full body of each triggered entry into the work order.
    """
    if not ledger.save.active_lore_ids:
        return []
    by_id = {entry.id: entry for entry in ledger.world.lorebook}
    lines = ["世界书（本场可能相关的背景设定，按此设定写）："]
    for entry_id in ledger.save.active_lore_ids:
        entry = by_id.get(entry_id)
        if entry:
            lines.append(f"- [{entry.id}] {entry.body}")
    return lines


def goals_block(ledger: Ledger) -> list[str]:
    """剧情目标（M14）：玩家在导演窗口设立的方向（玩家目标与 NPC 目标），
    编剧写作时必须自然地向其引导。置于预设段之后（不可裁块）。"""
    goals = [g for g in ledger.save.goals if g.status == "active"]
    if not goals:
        return []
    lines = ["剧情目标（玩家设立的方向）："]
    for g in goals:
        tag = "大目标/主线" if g.kind == "big" else "小目标/支线"
        owner = "玩家" if g.subject in {"", "player"} else (
            ledger.world.npcs[g.subject].name if g.subject in ledger.world.npcs else g.subject
        )
        npc_tag = f"（关联：{ledger.world.npcs[g.npc_id].name}）" if g.npc_id and g.npc_id in ledger.world.npcs else ""
        lines.append(f"- [{tag}·{owner}] {g.text}{npc_tag}")
    lines.append(
        "引导纪律：把本回剧情**自然地**朝这些目标推进——NPC 提起线索、机会现前、冲突冒头；"
        "一次只推进一小步，禁止一轮内生硬给出全部结果；目标之外的自由展开不受限制。"
        "目标归属者为 NPC 时，由**该 NPC** 在戏里主动推进：NPC 在场 → 让她自然提及（几句话、"
        "一个试探）；NPC 不在场 → 安排她主动来找玩家（登门/路遇/托人带话）。"
    )
    return lines


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
        "角色不是你（知情总纲）：事件日志、幕后注、世界书都是你案头的编剧资料，角色本人并不知道。"
        "每个角色开口前，核对台词是否在他自己的已知范围内——他亲历的事（日志行标注的在场者）、"
        "他来历该知道的事（人物卡）、剧情里有人当场告诉过他的事。"
        "在场 NPC 以工作单「已知集」清单为准：清单里没有的事一律不知道——包括其他区域的旧事，"
        "哪怕主角就是眼前这位玩家；清单外的公开旧事只能以「听说的」口吻或不提"
        "（轰动大事可作风闻，鸡毛小事传不出远门）；"
        "幕后注与私密事件的真相绝不能从不该知道的人嘴里说出（知情者当场坦白除外，那是新戏）；"
        "无人物卡的即兴角色只知道眼前可见的东西。",
        "位置是快照不是事实：NPC 的最后位置/在场名单是最近一次记录的快照，可能已过期。"
        "在场名单标注了每人最后被目击的时刻——刚目击的可放心写他在场；隔了半天的，"
        "依据此人的人物卡与最近经历合理推断他此刻可能在何处——找到、扑空、他挪了地方都是合理的叙事，"
        "不要机械地把快照位置当作他此刻的所在。",
        "前情已在世界里发生（事件日志是它的记录）：玩家已经历过的事不需要你复述或回顾，"
        "直接接续当下的戏，只处理本回合的输入；不要再交代一遍已经写过的剧情。",
        "深抉择纪律：标（配 Actor）的 NPC 撞上深抉择（内心判断 / 涉密反应 / 是否信任）时，"
        "你**不得替他决定**，必须在输出里填 actor_questions（npc_id / question / context），引擎会派他的 Actor 决定后回来再成文；"
        "标（导演代笔）的 NPC 或普通对话由你直接写出即可。context 只写该 NPC 本人会知道的情境，"
        "禁止写只有你知道的动机、私密或幕后注。",
        "不要提到 AIWorld、系统、编剧、玩家输入等元信息，不要打破第四面墙——只写玩家在故事里能感知到的内容。",
    ]


def writer_output_format() -> list[str]:
    """JSON 信封说明，置于近因区（prompt 尾部，紧贴玩家输入消息）。"""
    return [
        "",
        "输出必须是 JSON 对象，字段：",
        '{"prose": "正文全文（必填，直接成稿）", "summary": "一句话剧情摘要（不超过30字）", "actor_questions": []}',
        "prose：本场戏正文，唯一正文字段。",
        "summary：本场发生的核心事件摘要，供事件日志使用。",
        "actor_questions：需要派 Actor 的深抉择列表，没有则空数组。",
        "时间、地点、在场者、私密情境等世界变化都由引擎从你的正文里结算——你不需要、也不要输出这些字段，只需把变化在正文里写清楚（如「天黑了」「走出网吧」「王蓉先走了」）。",
        "禁止输出 location、participants、time_hint、private 或任何其他字段。",
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
    No private notes, no other NPCs' secrets, no lore candidates, no goals,
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
    if npc.personal_secrets:
        parts.append(f"你心里的事（只有你自己知道，绝不对外人说）：{npc.personal_secrets}")
    parts += scene_snapshot_block(world, ledger, scene_id)
    mem = ledger.experiences(npc_id, npc_id)[-memory_limit:]
    if mem:
        parts += ["你记得的事：", *mem]
    return "\n".join(parts)


def build_director_chat_system(
    world: WorldContent,
    ledger: Ledger,
    scene_id: str,
) -> str:
    """Work order for the director-window chat (OOC).

    Reads the same objective ledger blocks as the writer, plus the list of
    backstage actions the player may request and the requirement to end a
    discussion with a copyable input suggestion.
    """
    scene_id = scene_id or ledger.save.player_scene or "main_street"
    present_ids = ledger.present_at(scene_id)
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    names = [world.npcs[pid].name if pid in world.npcs else pid for pid in present_ids]

    parts: list[str] = [
        "你是 AIWorld 的导演，玩家正在戏外（OOC）和你讨论。你不是正文执笔者，一切建议都要玩家采纳后才生效。",
        "世界概要（不可违背）：",
        *world.meta.summary,
        "当前时间：" + (ledger.save.clock or "-"),
        "当前场景：" + (scene.name if scene else scene_id),
        "在场：" + ("、".join(names) or "暂无"),
    ]
    parts += event_log_block(world, ledger, include_ids=True)
    parts += goals_block(ledger)
    parts += known_set_block(world, ledger, present_ids, ledger.save.player_scene)
    parts += active_lore_block(ledger)
    parts += private_notes_block(world, ledger, present_ids)
    parts += character_block(world, ledger, present_ids, include_ids=True)

    parts += [
        "可执行的幕后操作（只有玩家明确要求时才填 action，普通闲聊绝不填）：",
        "- 静默覆写 override：玩家声明某人/某物在哪或去做某事 → payload {subject, location}",
        "- 记忆注入 inject_memory：玩家要求给某 NPC 私下注入一条记忆 → payload {npc_id, memory}（只有他知道）",
        "- 事件访问改判 access_rejudge：玩家要求某事件公开或私密 → payload {event_id, known_by: [知情者...] 或 null}",
        "- 剧情目标 set_goal：玩家要求设立/废弃剧情目标 → payload {text, kind: big|small, subject?: player|npc_id, big_goal_id?, npc_id?}（设立）或 {goal_id, status: abandoned}（废弃）；大目标=主线，小目标=支线；subject=目标归属者（默认 player，玩家替 NPC 设立时给该 NPC id），活动目标上限 6 条",
        "（人物卡编辑、Actor 档位、转正/场景注册一律由世界工作台直接编辑，不走导演窗口。）",
        "纪律：不得替玩家决定是否执行；一旦要执行必须返回 action 供玩家确认。",
        "讨论剧情时，若结论明确，最后给一句简短的输入建议（玩家可直接复制进正文框）。",
        "",
        "输出必须是 JSON 对象，字段：",
        '{"reply": "你的回复文本（直接回答玩家，必填）", "action": null | {"type": "override|inject_memory|access_rejudge|set_goal", "payload": {"字段": "值"}}}',
        "reply：给玩家的戏外回复。",
        "action：只有当玩家明确要求执行幕后操作时才填；否则为 null。",
        "注意：payload 里的 npc_id / subject / goal_id 必须使用角色 id（如 npc_zhuming，见角色资料行的 id 标注），不要用中文名字。",
        "禁止输出其他字段。",
    ]
    return "\n".join(parts)


def build_audit_work_order(world: WorldContent, ledger: Ledger, scene_id: str) -> str:
    """Audit work order: settle world side effects from the prose.

    The audit infers time advance, location, presence, privacy and one-shot
    vs registered scenes from the narrative, then judges goal completion
    (M14 剧情目标) and lifecycle.
    """
    scene_id = scene_id or ledger.save.player_scene or "main_street"
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present_ids = ledger.present_at(scene_id)
    names = [world.npcs[pid].name if pid in world.npcs else pid for pid in present_ids]
    scene_list = "、".join(f"{s.id}（{s.name}）" for s in world.scenes)
    goals = [g for g in ledger.save.goals if g.status == "active"]
    goal_lines = "；".join(f"[{g.id}]（{'主线' if g.kind == 'big' else '支线'}）{g.text}" for g in goals) or "（无）"
    return "\n".join(
        [
            "你是 AIWorld 的世界审计：玩家采纳一条正文后，你从正文里结算世界的副作用，并判定剧情目标与生命周期。",
            "只返回 JSON，格式如下：",
            '{"location": "场景id", "scene_name": "地点名（新地点给中文名）", "register_scene": false,'
            ' "participants": ["player", "npc_zhuming"], "private": false, "delta_minutes": 0,'
            ' "npc_moves": [{"npc_id": "npc_zhuming", "location": "zhuming_home", "scene_name": "朱明家"}],'
            ' "completed_goal_ids": ["达成目标id"], "lifecycle": [{"npc_id": "npc_zhuming", "status": "retired"}]}',
            "判定规则：",
            "- location：正文里玩家此刻所在之处。玩家在正文中明确移动（离开/去别处/回家）时更新，否则保持当前场景。",
            "- participants（与 location 联动，二选一）：",
            "  · 玩家**没移动**：以工作单给出的在场名单为**默认基线**——正文没有明确的进出场就原样继承整份名单；"
            "只有正文明确写出某人离开（走掉/告辞）才移除，明确写出新人到场并需记入史实才添加。"
            "正文用\"她/他\"等代词指代的在场者视为仍在场（代词指代不清时保守保留原名单）；"
            "摊主、路人等叙事背景人物不进名单——他们只是舞台布景，不是这段史实的参与者。"
            "location 与 participants 都不要凭空改动。",
            "  · 玩家**移动了**：留在原地的人不进名单（他们的位置自然停在原地，不需要你输出\"某人留在原处\"）；"
            "正文里在**新场景出现并互动**的角色——无论同行、被玩家找到、还是主动搭话——都计入 participants"
            "（他们亲身参与了这段史实）。",
            "- npc_moves：正文明确写出某个 NPC **离开去了别处**（回家/回店/告辞离去）时输出，"
            "每项 {\"npc_id\": \"...\", \"location\": \"英文id\", \"scene_name\": \"中文名（新地点给）\"}。"
            "没写去向就不要输出（\"走了\"没说去哪 → 不猜，宁可不记不错记）；"
            "玩家自己的移动不用输出（location 已覆盖）。多数回合为空数组。",
            "- 场景若已在场景表里，用其 id；正文进入未注册的新地点时，location 给一个英文 id，scene_name 给中文名。"
            "register_scene：玩家声明要去/回访/会复用该地点时为 true（注册为可导航场景）；"
            "剧情顺笔的一次性舞台（如今晚的草地、密室）为 false（不进导航集，显示名仍可用）。",
            "- private：按正文内容判定这场戏是否只限在场者知道——判据是\"外人会不会知道这事发生过\"，不单看地点。"
            "私下交底、咬耳朵只让对方听见、无人看见的交易、隐蔽处行事 → true；"
            "当众冲突、大庭广众下的对话、旁人可见可闻的活动 → false。",
            "- delta_minutes：你估计\"这场戏实际经过了多少分钟\"——以正文结束那一刻故事内的时钟为准。"
            "判定依据是玩家经历了什么，不是文本里出现了什么时间词："
            "对话/商量/闲聊 → 5~15；一顿饭 → 30~60；顺笔赶路 → 按路程；"
            "干活/训练一个下午 → 120~240；睡觉 → 480。"
            "给 0 的情况：只是说到时间（\"明天见\"\"你昨天答应的\"\"三点在那碰面\"——被说的不是被经历的），"
            "以及没有新的经历性事件（原地续聊）。",
            "- completed_goal_ids：正文已达到目标文本所述（小目标=当事达成；大目标=关键真相/冲突已解决）。"
            "只推进未达成的不填——推进由编剧纪律负责，审计只判终点。"
            "注意：目标归属者为 NPC 时，该目标 NPC 提及/推进目标只是推进（如王蓉提起接货），"
            "只有当正文里目标所述之事真正发生（如玩家答应了）才判完成——推进不算完成。",
            "- lifecycle：按正文语义识别角色退场（死亡/永久离开）→ retired。",
            "当前时间：" + (ledger.save.clock or "-"),
            "当前场景：" + (scene.name if scene else scene_id),
            "在场：" + ("、".join(names) or "暂无"),
            "已注册场景（格式 = id（名），location 必须从这里选 id，新地点才自造英文 id）：" + (scene_list or "（无）"),
            "活动目标：" + goal_lines,
            *world.meta.summary,
        ]
    )


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
        # J 编剧准则（创作与写作要求，紧跟金科玉律）
        if preset.writer_guidelines:
            parts.append("编剧准则（创作与写作要求，必须遵循）：")
            parts.append(preset.writer_guidelines)
        # G 剧情目标（玩家设立的方向，紧跟预设段：写作须自然向目标引导）
        parts += goals_block(ledger)
        # D 玩家资料 + 在场 NPC（含 id 标注，编剧须用规范 id）
        parts += character_block(world, ledger, present_ids, include_ids=True)
        # H 幕后注（机密，与金科玉律呼应双保险）
        parts += private_notes_block(world, ledger, present_ids)
        # F 场景快照（此刻环境：当前时间/在场/场景/可感知）
        parts += scene_snapshot_block(world, ledger, scene_id)
        # G 在场 NPC 已知集（机械知识边界，取代旧近况块）
        parts += known_set_block(world, ledger, present_ids, scene_id)
        # I 可选素材（世界书命中，未来 token 超支时最先可裁）
        parts += active_lore_block(ledger)
        # K 输出格式（指令，近因区）
        parts += writer_output_format()
        # E 事件日志（更早摘要在前 → 最近原文在后，收尾紧贴玩家输入以便续写）
        parts += event_log_block(world, ledger)
        return "\n".join(parts)

    if viewer.startswith("actor_"):
        npc_id = viewer[len("actor_"):]
        return build_actor_work_order(
            world, ledger, npc_id, scene_id or ledger.save.player_scene or "main_street"
        )

    raise ValueError(f"unknown viewer: {viewer}")