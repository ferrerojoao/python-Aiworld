from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import WriterOutput


def build_writer_system(
    world: WorldContent,
    ledger: Ledger,
    *,
    scene_id: str,
    preset: NarrativePreset | None = None,
) -> str:
    """Assemble the writer's work order (single agent = director + storyteller).

    The writer sees everything the director used to see (event log, NPC
    recent history, private notes, lore candidates, hooks, conflicts) and is
    instructed to plan beats mentally, then write the prose directly.
    """
    scene_id = scene_id or ledger.save.player_scene or "main_street"
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present_ids = ledger.present_at(scene_id)
    present_names = [
        world.npcs[pid].name if pid in world.npcs else pid for pid in present_ids
    ]
    npc_names = "、".join(present_names) or "暂无"
    preset = preset or world.presets

    parts = [
        "你是 AIWorld 的编剧：你同时负责排戏的走向和正文执笔。",
        "拿到玩家输入后，先在脑中排好本场戏的节拍（先后顺序、谁说什么、情绪转折），再一次性直接写成正文。",
        "世界概要（硬规则，不可违背）：",
        *world.meta.summary,
    ]

    # 角色资料：主角与在场 NPC 放在同一层级
    player = ledger.save.player
    character_lines = ["角色资料："]
    character_lines.append(
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
            character_lines.append(
                f"[{npc.name}]{ticket} 名字：{npc.name}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}"
            )
        else:
            character_lines.append(f"[{pid}] 名字：{pid}")
    parts += character_lines
    parts += [
        "深抉择纪律：",
        "标（配 Actor）或（玩家点名强制使用 Actor）的 NPC 撞上深抉择（内心判断 / 涉密反应 / 是否信任）时，"
        "你**不得替他决定**，必须在输出里填 actor_questions（npc_id / question / context），"
        "引擎会派他的 Actor 决定后回来再成文。",
        "标（导演代笔）的 NPC 或普通对话由你直接写出即可，不填 actor_questions。",
        "actor_questions 的 context 只写该 NPC 本人会知道的情境，禁止写只有你知道的动机、私密或幕后注。",
    ]

    # --- 账本工作单：事件日志 / 近况 / 幕后注 / 世界书候选 / 钩子 / 矛盾 ---
    recent = ledger.narratives[-10:]
    if recent:
        parts.append("事件日志（世界近期发生的事，全部向你开放）：")
        for ev in recent:
            summary = (ev.get("summary") or (ev.get("body") or ""))[:60]
            line = f"- {ev.get('at', '')} {summary}"
            if ev.get("player_input"):
                line += f"（玩家当时说：{ev['player_input'][:30]}）"
            parts.append(line)

    near_lines = []
    for pid in present_ids:
        npc = world.npcs.get(pid)
        if npc is None:
            continue
        if npc.private_note:
            parts.append(f"幕后注（仅你可读，绝不写进正文，也不得让任何角色知道）：[{npc.name}] {npc.private_note}")
        mem = ledger.experiences(npc.id, npc.id)[-2:]
        if mem:
            near_lines.append(f"[{npc.name}] 最近经历：{'；'.join(mem)}")
    if near_lines:
        parts.append("在场 NPC 近况：")
        parts.extend(near_lines)

    cands = ledger.lore_candidates(scene_id, present_ids)
    if cands:
        parts.append("世界书候选（本场可能相关，只有摘要；要用就按此设定写）：")
        for c in cands:
            parts.append(f"- [{c['id']}] {c['summary']}")

    open_hooks = [h for h in ledger.save.hooks if h.status == "open"]
    if open_hooks:
        parts.append("开放钩子（悬而未决的事，可作素材）：")
        for h in open_hooks:
            parts.append(f"- {h.text[:60]}")

    open_conflicts = [c for c in ledger.save.pending_conflicts if c.status == "open"]
    if open_conflicts:
        parts.append("待澄清矛盾（玩家可选无视，你避免再扩散）：")
        for c in open_conflicts:
            parts.append(f"- {c.desc[:60]}")

    if preset.director_guidelines:
        parts.append("导演准则：")
        parts.append(preset.director_guidelines)
    else:
        parts.append("叙述预设：")
        parts.append(preset.style)
        parts.append(preset.description_style)
    if preset.storyteller_preset:
        parts.append("写作要求：")
        parts.append(preset.storyteller_preset)

    parts += [
        "在场：" + npc_names,
        "当前场景：" + (scene.name if scene else "主街"),
        scene_text if (scene_text := scene.perceivable if scene else "未知场景") else "",
        "不要提到 AIWorld、系统、编剧、剧本指令、玩家输入等元信息。",
        "不要打破第四面墙，只写玩家在故事里能感知到的内容。正文按第二人称写玩家。",
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
    return "\n".join(parts)


async def run_writer(
    llm,
    world: WorldContent,
    ledger: Ledger,
    player_input: str,
    *,
    rule_bundle: dict | None = None,
    scene_id: str | None = None,
    preset: NarrativePreset | None = None,
    actor_decisions: str = "",
    rewrite_note: str = "",
    model: str = "fake",
    temperature: float = 0.8,
) -> WriterOutput:
    """Single-agent writer: decides the scene and writes the prose in one call.

    When the output carries actor_questions, the caller runs the isolated
    Actor for each and re-invokes with actor_decisions set.
    """
    system = build_writer_system(world, ledger, scene_id=scene_id or "", preset=preset)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"玩家输入：{player_input}"},
    ]
    if rule_bundle:
        messages.append({"role": "user", "content": f"规则段预结算：{rule_bundle}"})
    if actor_decisions:
        messages.append({"role": "user", "content": f"以下深抉择已由对应 NPC 亲自决定，按此成文：\n{actor_decisions}"})
    if rewrite_note:
        messages.append({"role": "user", "content": f"改写要求：{rewrite_note}"})

    data = await llm.complete_json(
        messages,
        WriterOutput,
        model=model,
        temperature=temperature,
    )
    return WriterOutput.model_validate(data)