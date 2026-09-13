"""Work order assembly (TDD §6): objective ledger snapshot + subjective contract.

Objective blocks are pure data (facts queried from the ledger/content pack);
subjective contracts are instructions (identity, discipline, output format).

Assembly order（2026-09-11 三级分层）：写作纪律**分级**并连成一块梯队，排在身份之后；
资料区紧随其后、完整不被切开；事件日志收尾紧贴玩家输入：

    一级 输出格式 —— 格式出错 = 界面直接失败、玩家无法继续；全表唯一的硬边界
    二级 情节合理性 —— 信息边界 / 抉择归属 / 设定一致 / 连续性 / 不出戏
    三级 文风与剧情倾向 —— 预设；默认遵循，可按情境灵活

    实际顺序：A 身份与任务 → 二级 → 三级 → 一级 →
    资料区（世界概要 / 世界书命中 / 角色资料 / 幕后注 / 场景快照 /
    已知集 / 剧情目标——不标规则等级，与事件日志同性质）→
    E 事件日志（最近原文在后） → 玩家输入消息

    段落抬头只写等级名、不带解释句（同日用户判定自我说明是废话）；
    一级置于梯队末尾而非资料区之后，是为了让资料区连成整块（同日用户调整）。

分级的目的：让模型分清「错了会坏数据」与「这样写更好」。此前两者混在同一个
「绝对不可违背」的箱子里，模型只能一律照办——越写越保守，什么都不敢写。

同日另补（深抉择流程修复）：
- 二级「抉择归属」条末尾下发**本轮可上缴名单**（在场 ∩ 配 Actor），编剧不必再从
  各人名后的括号自行推断谁不能上缴；
- 一级收下 ``actor_questions`` 的**条目结构**（此前错放在二级的括号里），并给带值样例。
"""

from __future__ import annotations

import datetime as dt

from app.ledger.queries import Ledger
from app.rules.lorebook import subject_hit_ids
from app.world.models import NarrativePreset, WorldContent


# ---------------------------------------------------------------------------
# 客观层 · 账本快照（纯函数，只陈述事实，不含指令）
# ---------------------------------------------------------------------------

def world_summary_block(world: WorldContent) -> list[str]:
    """世界概要：与事件日志同性质的**资料**，不标规则等级（2026-09-11）。

    世界设定天然公开、人人可引（REQ 〇章）。抬头不再写「硬规则 / 不可违背」——
    规则等级只留给真正需要分级的三级写作纪律；资料一律平等地摆在资料区，
    模型自会取材，不需要额外的强制标签。
    """
    return [
        "世界概要：",
        *world.meta.summary,
    ]


def character_block(world: WorldContent, ledger: Ledger, present_ids: list[str], include_ids: bool = False) -> list[str]:
    """主角资料 + 在场 NPC 名片（含 Actor 档位标注，可选附带 id）。

    主角与 NPC 分两段（2026-09-13 主角入人物表后仍保留）：主角是对话对象，
    有独立视角，混在"在场 NPC"里读起来会像第三方。主角卡在 NPC 段被跳过。
    """
    player = world.player()
    player_name = world.player_name()
    lines = ["玩家资料："]
    if player is not None:
        lines.append(
            f"[主角] 名字：{player.id}；外貌：{player.appearance or '未设定'}；"
            f"人格：{player.persona or '未设定'}"
        )
    else:
        lines.append(f"[主角] 名字：{player_name}；外貌：未设定；人格：未设定")
    npc_lines = []
    for pid in present_ids:
        if pid == player_name:
            continue  # 主角另有独立段，不重复列
        npc = world.npcs.get(pid)
        if npc:
            ticket = "（配 Actor）" if npc.has_actor else "（导演代笔）"
            npc_lines.append(
                f"[{npc.id}]{ticket} 外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}"
            )
        else:
            npc_lines.append(f"[{pid}]")
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
        return scene.id if scene else (ev.get("location_name") or loc)

    def _ctx_tag(ev: dict) -> str:
        parts = []
        where = _where(ev)
        if where:
            parts.append(where)
        pids = ev.get("participants", [])
        if pids:
            parts.append("在场者：" + "、".join(pids))
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


def player_display_name(ledger: Ledger) -> str:
    """主角在名单/契约里的显示名。

    主角是人物表里的一员（2026-09-13），名字就是他自己的中文名。唯一保留的
    退化：作者若把主角名写成占位符「你」，Actor 视图里会出现「你（玩家）」，
    与契约「你 = 你自己」正面打架——此时退化为「玩家」（2026-09-11 原逻辑）。
    """
    name = ledger.world.player_name().strip()
    return name if name and name != "你" else "玩家"


def scene_snapshot_block(
    world: WorldContent,
    ledger: Ledger,
    scene_id: str,
    exclude: list[str] | None = None,
) -> list[str]:
    """场景快照：当前时间 / 在场名单 / 当前场景 / 可感知区。

    主角是人物表里的普通一员（2026-09-13），其位置同样由事件流水推导，因此
    名单里正常就有他，只是**多一个「（玩家）」标注**——Actor 视图据此认出谁是
    对话对象。真正的特例只剩"移动途中"：工作单的 scene 是本场正要写的场景
    （可能是本轮才解析出的目的地），主角的位置事实还停在旧场景，此时把他补进
    名单最前——工作单永远是主角所在场景的工作单。

    ``exclude`` 供 Actor 视图剔掉自己。
    """
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    skip = set(exclude or ())
    player = ledger.world.player_name()
    present_ids = [pid for pid in ledger.present_at(scene_id) if pid not in skip]
    if player not in present_ids and player not in skip:
        present_ids = [player, *present_ids]
    clock = ledger.save.clock or ""
    name = player_display_name(ledger)
    parts = []
    for pid in present_ids:
        ev = ledger.where_is(pid)
        seen = _rel_seen(clock, ev.get("at", "")) if ev else ""
        if pid == player:
            label = name if name == "玩家" else f"{pid}（玩家）"
        else:
            label = pid
        parts.append(f"{label}（最后目击：{seen}）" if seen else label)
    return [
        "当前时间：" + (clock or "-"),
        "在场（括号内 = 该角色最后被记录在此的时刻，久未见面的要考虑他是否还在）："
        + ("、".join(parts) or "暂无"),
        "当前场景：" + (scene.id if scene else "主街"),
        scene.perceivable if scene else "未知场景",
    ]


def known_set_block(world: WorldContent, ledger: Ledger, present_ids: list[str], scene_id: str, per_npc: int = 5) -> list[str]:
    """在场 NPC 已知集：机械计算的知识边界（亲历 ∪ known_by ∪ 同区域公开）。

    取代旧"近况块"——写手与 QC 共用同一份清单，单一事实源。
    """
    lines = []
    player_name = ledger.world.player_name()
    for pid in present_ids:
        if pid == player_name:
            continue  # 主角另有独立段，不重复列
        npc = ledger.world.npcs.get(pid)
        if npc is None:
            continue
        mem = ledger.known_set(pid, scene_id, per_npc)
        if mem:
            lines.append(f"[{npc.id}] 知道的事：{'；'.join(mem)}")
        else:
            lines.append(f"[{npc.id}] 知道的事：（无——该角色的知识从眼前开始）")
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
    保密要求由抬头统一声明一次，各条目只列内容（2026-09-11 去重）。
    """
    notes_lines: list[str] = []
    player = world.player()
    player_name = world.player_name()
    if player is not None and (player.private_note or player.personal_secrets):
        notes = []
        if player.private_note:
            notes.append(player.private_note)
        if player.personal_secrets:
            notes.append(f"[{player.id} 自知] {player.personal_secrets}")
        notes_lines.append(f"[主角·{player.id}] {'；'.join(notes)}")
    for pid in present_ids:
        if pid == player_name:
            continue  # 主角的幕后注已在上面独立成条
        npc = world.npcs.get(pid)
        if npc and (npc.private_note or npc.personal_secrets):
            notes = []
            if npc.private_note:
                notes.append(npc.private_note)
            if npc.personal_secrets:
                notes.append(f"[{npc.id} 自知] {npc.personal_secrets}")
            notes_lines.append(f"[{npc.id}] {'；'.join(notes)}")
    if not notes_lines:
        return []
    return [
        "幕后注（仅你可读，只作暗示铺垫用）：",
        *notes_lines,
    ]


def active_lore_block(ledger: Ledger, present_ids: list[str] | None = None) -> list[str]:
    """World book entries for this turn (常驻 + 归属 + save.active_lore_ids).

    三条通道（2026-09-13 归属通道上线）：

    - **常驻**（always_on）每轮必注入、不占触发名额、不吃 5 条上限；
    - **归属**（subject）归属者在场即注入、同样不占关键词名额（每人上限见
      ``LORE_SUBJECT_CAP``）——"一个 NPC 的信息 = 卡 + 归属条目"才齐全，
      他被谈论而本人不在场时，仍回到关键词通道；
    - **触发**（关键词命中玩家输入/已采纳正文）走 save.active_lore_ids，全局 5 条。
    """
    lines = ["世界书（本场可能相关的背景设定，按此设定写）："]
    by_id = {entry.id: entry for entry in ledger.world.lorebook}
    seen: set[str] = set()
    count = 0
    for entry in ledger.world.lorebook:
        if entry.always_on and entry.id not in seen:
            seen.add(entry.id)
            count += 1
            lines.append(f"- 【常驻】{entry.body}")
    for entry_id in subject_hit_ids(ledger.world, list(present_ids or [])):
        entry = by_id.get(entry_id)
        if entry and entry.id not in seen:
            seen.add(entry_id)
            count += 1
            lines.append(f"- 【归属·{entry.subject}】{entry.body}")
    for entry_id in ledger.save.active_lore_ids:
        entry = by_id.get(entry_id)
        if entry and entry.id not in seen:
            seen.add(entry_id)
            count += 1
            lines.append(f"- {entry.body}")
    if not count:
        return []
    return lines


def goal_brief(ledger: Ledger, goal, *, include_ids: bool = False) -> str:
    """单个目标的单行摘要：``[id] [大目标/主线·玩家] 文本（关联：X）``。

    subject 空串 = 主角的目标（默认），否则是该角色的中文名（2026-09-13 改口径，
    此前是魔法串 "player"）。
    """
    id_part = f"[{goal.id}] " if include_ids else ""
    tag = "大目标/主线" if goal.kind == "big" else "小目标/支线"
    owner = goal.subject or "玩家"
    npc_tag = (
        f"（关联：{goal.npc_id}）"
        if goal.npc_id and goal.npc_id in ledger.world.npcs
        else ""
    )
    return f"{id_part}[{tag}·{owner}] {goal.text}{npc_tag}"


def _goals_discipline(has_big: bool, has_loose: bool, has_npc: bool) -> str:
    """引导纪律：按实际存在的目标形态裁剪（不空谈不存在的层级）。

    2026-09-12 分层——大目标是方向盘（每回至少一步、且沿其下子目标走），
    小目标择机落地（场景/在场者相关才推）。这是"大目标与小目标不再只是标签
    不同"的落点：父给方向、子给动作。
    """
    lead = "把本回剧情**自然地**朝这些目标推进——NPC 提起线索、机会现前、冲突冒头；"
    parts: list[str] = []
    if has_big:
        parts.append(
            lead
            + "**大目标是方向盘**：每回至少推进主线一步，且优先沿它下面的小目标走——"
            "小目标就是这条主线的「下一步该发生什么」。"
        )
        if has_loose:
            parts.append("未挂靠的支线只在与当前场景/在场者相关时顺带推一推。")
    elif has_loose:
        parts.append(lead + "只在与当前场景/在场者相关时推一推。")
    # 2026-09-12 去否定化：原句下半是"禁止一轮内生硬给出全部结果"，改为正向——
    # 说清"结果放在哪里"（后续场次），而不是禁止某个动作。
    parts.append("一次只推进一小步，目标的全部结果留给后面的场次逐拍铺开；目标之外的自由展开不受限制。")
    if has_npc:
        parts.append(
            "目标归属者为 NPC 时，由**该 NPC** 在戏里主动推进：NPC 在场 → 让她自然提及"
            "（几句话、一个试探）；NPC 不在场 → 安排她主动来找玩家（登门/路遇/托人带话）；"
            "只对挂在未完成大目标下的子目标这么做（防止支线无限拉人登门）。"
        )
    return "引导纪律：" + "".join(parts)


def goals_block(ledger: Ledger, include_ids: bool = False) -> list[str]:
    """剧情目标（M14）：玩家在导演窗口设立的方向（玩家目标与 NPC 目标），
    编剧写作时必须自然地向其引导。

    大目标 = 章节（往哪去），小目标 = 节拍（下一步做什么）：按 ``big_goal_id``
    渲染成两级树并标章节进度——父子层级在此第一次真正参与运转（2026-09-12）。
    位置未动：仍在资料区尾部、事件日志之前（用户 2026-09-12 明确"位置不要动"）。

    ``include_ids``：导演窗口需要 id 才能废弃/挂父（此前一律不吐 id，导致
    这两个动作实际填不出来）；编剧不需要 id，保持 False。
    """
    from app.ledger.goals import child_progress, goal_tree

    tree, loose = goal_tree(ledger)
    if not tree and not loose:
        return []
    lines = ["剧情目标（玩家设立的方向）："]
    for big, kids in tree:
        done, total = child_progress(ledger, big.id)
        head = "- " + goal_brief(ledger, big, include_ids=include_ids)
        if total:
            head += f"　（子目标 {done}/{total} 已完成）"
        lines.append(head)
        for kid in kids:
            lines.append("　　· " + goal_brief(ledger, kid, include_ids=include_ids))
    if loose:
        lines.append("未挂靠的支线：")
        for goal in loose:
            lines.append("- " + goal_brief(ledger, goal, include_ids=include_ids))
    has_npc = any(g.npc_id for _b, kids in tree for g in (_b, *kids)) or any(
        g.npc_id for g in loose
    )
    lines.append(_goals_discipline(bool(tree), bool(loose), has_npc))
    return lines


def audit_goals_lines(ledger: Ledger) -> list[str]:
    """审计看到的活动目标：带 id 的两级树 + 章节进度。

    父子层级给审计一个**客观锚点**：判大目标完成时能看到"其下子目标 x/y 已完成"。
    只呈现证据、不设硬门锁——判不判仍由审计定（避免机械兜底，也避免误伤
    "玩家绕道达成主线"）。
    """
    from app.ledger.goals import child_progress, goal_tree

    tree, loose = goal_tree(ledger)
    if not tree and not loose:
        return ["（无）"]
    lines: list[str] = []
    for big, kids in tree:
        done, total = child_progress(ledger, big.id)
        head = goal_brief(ledger, big, include_ids=True)
        if total:
            head += f"——其下子目标 {done}/{total} 已完成"
        lines.append(head)
        for kid in kids:
            lines.append("  └ " + goal_brief(ledger, kid, include_ids=True))
    for goal in loose:
        lines.append(goal_brief(ledger, goal, include_ids=True))
    return lines


# ---------------------------------------------------------------------------
# 主观层 · 角色契约（按 agent 身份选择；每个契约 = 身份/写作纪律/输出格式）
# ---------------------------------------------------------------------------

def writer_identity() -> list[str]:
    return [
        "你是 AIWorld 的编剧：你同时负责排戏的走向和正文执笔。",
        "拿到玩家输入后，先在脑中排好本场戏的节拍（先后顺序、谁说什么、情绪转折），再一次性直接写成正文。",
    ]


def writer_story_rules(roster: list[str] | None = None) -> list[str]:
    """二级 · 情节合理性：信息边界 / 抉择归属 / 设定一致 / 连续性 / 不出戏。

    2026-09-11 由「金科玉律（绝对不可违背）」改名并**降级**：这些规则违反会让
    戏说不通（重掷或改写即可救），但不损坏数据、不影响系统运转——不该与输出
    格式同处「绝对不可违背」的等级。段落抬头只保留等级名，**不带解释句**
    （同日用户判定：抬头的自我说明是废话，等级靠位置与命名即可读出来）。

    ``roster`` 是**本轮可上缴名单**（在场 ∩ 配 Actor，由 ``build_work_order`` 算）。
    这份名单必须显式下发：此前编剧只能自己聚合各人名后「（配 Actor）/（导演代笔）」
    的括号去推谁不能上缴，推错一格就会把无票角色的深抉择上缴上去，而引擎按票
    丢弃 → 那一拍永远是空的（同日用户报的第二类漏洞）。字段结构不在这里讲——
    那属于一级「输出格式」。

    ``player_name`` 参数已于 2026-09-13 移除：context 的人称约定是**字段写法**，
    随字段定义一起搬到一级（二级先于一级，在这里提 context 时它还没被定义）。

    2026-09-12 去否定化：本节原有多处"不得 / 不要 / 绝不能"与枚举式禁令
    （最重的是「不出戏」里逐一列出 AIWorld、系统、编剧、玩家输入四个禁词），
    改为描述**目标状态**（把这一拍写到抉择点为止 / 正文就是玩家能感知到的一切）。
    用户实测：否定措辞越多的提示词，模型表现越差——枚举禁词等于把禁项递到
    模型眼前，注意力从写作挪去自查。
    """
    if roster:
        roster_clause = (
            "本轮可上缴深抉择的角色：" + "、".join(roster) + "。"
            "其余角色一律由你直接决定并写进正文。"
        )
    else:
        roster_clause = "本轮没有任何角色可上缴深抉择，所有抉择都由你直接决定并写进正文。"
    return [
        "【二级 · 情节合理性】",
        "信息边界（角色不是你）：事件日志、幕后注、世界书都是你案头的编剧资料，角色本人并不知道——"
        "每个角色开口前核对台词是否在他已知范围内（在场 NPC 见工作单「已知集」清单）；"
        "真相只经由知情者之口进入正文（知情者当场坦白，那是新戏）；"
        "无人物卡的即兴角色只知道眼前可见的东西。",
        # actor_questions / context 的**字段写法**不在这里展开（2026-09-13 用户指出：
        # 二级先于一级，此处提到 context 时它还没被定义）——只讲机制并指向一级。
        "抉择归属：标（配 Actor）的角色撞上深抉择（内心判断 / 涉密反应 / 是否信任）时，"
        "把这一拍写到抉择点为止，在 actor_questions 里上缴（字段写法见「一级 · 输出格式」）——"
        "这一拍交给他的 Actor 决定，引擎决定后再把整场写全；"
        "标（导演代笔）的角色或普通对话由你直接写出来。"
        + roster_clause,
        "设定一致（位置）：在场名单是最近一次记录的快照，可能已过期——结合每人最后被目击的时刻"
        "与其人物卡合理推断他此刻在哪，找到、扑空、他挪了地方都是合理的叙事。",
        "连续性：玩家已经历过的事属于背景，直接接续当下的戏，只处理本回合的输入。",
        "不出戏：正文就是故事世界里玩家能感知到的一切。",
    ]


def writer_style_block(preset: NarrativePreset) -> list[str]:
    """三级 · 文风与剧情倾向：默认遵循，可按情境灵活（2026-09-11）。

    这一级是**偏好**不是硬边界。写明等级不是给它降格，而是让模型敢于为了
    戏的张力偏离它——此前它与「绝对不可违背」的规则混在一起，模型只能
    一律照办，结果是越写越保守。抬头只留等级名，不带解释句（同日用户判定）。

    style_sample（文风示范，2026-09-11）：紧贴 guidelines 注入，只留
    「文风示范：」标记 + 原文——贴身禁令经用户实测后要求去掉（22:47 功能
    上线时带禁令，23:08 用户要求先试无禁令版）。空 = 不注入，零成本。
    """
    lines: list[str] = []
    if preset.writer_guidelines:
        lines += ["【三级 · 文风与剧情倾向】", preset.writer_guidelines]
    if preset.style_sample:
        if not lines:
            lines.append("【三级 · 文风与剧情倾向】")
        lines += ["文风示范：", f"「{preset.style_sample}」"]
    return lines


def writer_output_format(player_name: str = "玩家") -> list[str]:
    """一级 · 输出格式：最硬的一级（2026-09-11 用户定级）。

    格式一旦出错，前端直接解析失败、玩家看不到正文也无法继续——这一级没有
    「灵活处理」的余地，是全表唯一的硬边界。抬头只留等级名，不带解释句
    （同日用户判定：抬头的自我说明是废话）。

    位置：紧跟三级预设块之后，使 二级 → 三级 → 一级 连成完整梯队，资料区
    不被切开（2026-09-11 用户调整，此前置于资料区与事件日志之间）。

    同日迁入：``actor_questions`` 的**条目结构**。此前字段名塞在二级的括号里、
    模板里只有一个空数组，模型得自己猜 ``npc_id`` 填什么——填「你」「玩家」或
    自造 id 都能通过校验，然后静默匹配不上（用户追问点）。字段结构属于格式层，
    迁到一级并给出带值的样例。

    2026-09-13 再迁入：``context`` 的**人称约定与范围**。用户指出二级先于一级，
    读到"context 的人称约定"时 context 还没被定义（前向引用）——字段写法就该
    贴着字段定义。二级只留机制（何时停笔上缴）并指向本节。

    ``player_name`` 走 ``player_display_name``：context 里提到玩家一律写其
    **姓名**（2026-09-11 用户改口径，此前是写「玩家」）；主角名未设定时退化
    为「玩家」。
    """
    if player_name == "玩家":
        ctx_clause = "「玩家」"
    else:
        ctx_clause = f"玩家姓名「{player_name}」"
    return [
        "【一级 · 输出格式】",
        "输出必须是 JSON 对象，字段：",
        '{"prose": "正文全文", "summary": "一句话摘要（不超过30字）",'
        ' "actor_questions": [{"npc_id": "朱明", "question": "…", "context": "…"}]}',
        "prose：本场戏正文全文——从第一句话写到本场结束（每稿都是完整全文）。",
        "summary：本场戏从头到尾的核心事件摘要，供事件日志使用（与 prose 同样覆盖整场）。",
        "actor_questions：深抉择的提问清单，没有时给空数组 []；每项三个字段：",
        "  · npc_id：必须是在场名单方括号里逐字出现的角色名（如 朱明）。",
        "  · question：你要问他的那个抉择本身。",
        f"  · context：人称约定只约束这个字段（正文照旧）——以该 NPC 为「你」，"
        f"提到玩家一律写{ctx_clause}，一句只用一个人称；这个字段的范围就是该 NPC "
        "本人会知道的情境，你的判断、私密与幕后注留在戏外。",
        "时间、地点、在场者、私密情境等世界变化都由引擎从你的正文里结算——你只把变化写清楚"
        "（如「天黑了」「走出网吧」）。输出字段以上面三个为准。",
    ]


def actor_contract(npc_name: str, player_name: str = "玩家") -> list[str]:
    """NPC Actor 的身份、隔离纪律与输出格式（物理隔离工作单）。

    人称读法必须讲明（2026-09-11）：情境文本里的人称由 writer 侧约定产生
    （以该 NPC 为「你」、提到玩家写其**姓名**），这里给出对应的读法，两边
    对齐后就不再需要机械替换。``player_name`` 走 ``player_display_name``——
    主角名未设定（占位符「你」）时退化为「玩家」。
    """
    return [
        f"你正在扮演：{npc_name}。你的决定只用下面「你知道的事」里的信息。",
        "人称读法（先把这几个词认准）：",
        f"- 「你」= 你自己（{npc_name}）；情境里用「他/她/名字」这类第三人称提到你时，那也是在说你。",
        f"- 「{player_name}」= 你的对话对象（人类玩家），是另一个人，不是你。",
        "- 某件事的归属出现矛盾（像是你做的、又像不是你做的）时，以「你知道的事」清单为准："
        "清单里没有的事，就不是你做的。",
        "纪律（本契约的硬边界）：",
        "- 你说的话来自「你知道的事」；超出这个范围的部分，留给知道的人去说。",
        f"- 只输出你自己的决定（decision / action_hint / tone）；{player_name}的抉择留给本人。",
        '只返回 JSON，格式如下：',
        '{"decision": "你的决定", "action_hint": "你会做的动作/行为", "tone": "语气"}',
    ]


def build_actor_work_order(
    world: WorldContent,
    ledger: Ledger,
    npc_id: str,
    scene_id: str,
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
        *actor_contract(npc.id, player_display_name(ledger)),
        *world_summary_block(world),
        "你的档案（人物卡）：",
        f"姓名：{npc.id}；外貌：{npc.appearance or '未设定'}；人格：{npc.persona or '未设定'}",
    ]
    if npc.personal_secrets:
        parts.append(f"你心里的事（只有你自己知道）：{npc.personal_secrets}")
    # 在场名单剔掉自己；主角在名单里自带「（玩家）」标注，Actor 据此认人
    #（2026-09-13 主角入人物表后不再需要"显式补上玩家"那一步）。
    parts += scene_snapshot_block(world, ledger, scene_id, exclude=[npc_id])
    # 记忆口径与写手/QC 一致：known_set（亲历 ∪ known_by 含己 ∪ 听域内公开），
    # 条数上限统一由 world.meta.memory_limit 管，不再有第二处硬编码。
    mem = ledger.known_set(npc_id, scene_id, limit=world.meta.memory_limit)
    if mem:
        parts += ["你知道的事：", *mem]
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
    scene_id = scene_id or ledger.current_scene()
    present_ids = ledger.present_at(scene_id)
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    names = [pid for pid in present_ids]

    parts: list[str] = [
        "你是 AIWorld 的导演，玩家正在戏外（OOC）和你讨论。你不是正文执笔者，一切建议都要玩家采纳后才生效。",
        "世界概要：",
        *world.meta.summary,
        "当前时间：" + (ledger.save.clock or "-"),
        "当前场景：" + (scene.id if scene else scene_id),
        "在场：" + ("、".join(names) or "暂无"),
    ]
    parts += event_log_block(world, ledger, include_ids=True)
    # include_ids=True：导演要靠 id 废弃目标、把子目标挂到某个大目标下
    #（2026-09-12 之前一律不吐 id，导致 set_goal 的废弃/挂父实际填不出来）。
    parts += goals_block(ledger, include_ids=True)
    parts += known_set_block(world, ledger, present_ids, scene_id)
    parts += active_lore_block(ledger, present_ids)
    parts += private_notes_block(world, ledger, present_ids)
    parts += character_block(world, ledger, present_ids, include_ids=True)

    parts += [
        # action 的触发条件只在下方"输出字段"一节说一次（2026-09-12 去冗余）：
        # 此前抬头也重复了一遍"只在玩家明确要求时才填 action"。
        "可执行的幕后操作：",
        "- 静默覆写 override：玩家声明某人/某物在哪或去做某事 → payload {subject, location}",
        "- 记忆注入 inject_memory：玩家要求给某 NPC 私下注入一条记忆 → payload {npc_id, memory}（只有他知道）",
        "- 事件访问改判 access_rejudge：玩家要求某事件公开或私密 → payload {event_id, known_by: [知情者...] 或 null}",
        "- 剧情目标 set_goal：玩家要求设立/废弃剧情目标 → payload {text, kind: big|small, subject?, big_goal_id?, npc_id?}（设立）或 {goal_id, status: abandoned}（废弃）；"
        "大目标=主线（章节，往哪去），小目标=支线（节拍，下一步做什么）；subject=目标归属者（留空=主角的目标，玩家替某角色设立时给该角色的中文名）；"
        "小目标要用 big_goal_id 挂到某个大目标下（挂靠后才能多挂，也才能被编剧当作主线的下一步推进）；"
        "额度：大目标上限 2 条、单个大目标下子目标上限 3 条、未挂靠支线上限 2 条；大目标完成/废弃时其下子目标一并撤下；"
        "设立大目标不需要 big_goal_id（层级只有一层）",
        "- 角色退场 retire：玩家要求某人永久退场（死亡/远行/消失，不再出现在任何场景）→ payload {npc_id}；"
        "不可逆，退场者从此退出在场推导与主动调度",
        "（人物卡编辑、Actor 档位、转正/场景注册一律由世界工作台直接编辑，不走导演窗口。）",
        "纪律：action 是一份提案——玩家确认之后才生效。",
        "讨论剧情时，若结论明确，最后给一句简短的输入建议（玩家可直接复制进正文框）。",
        "",
        "输出必须是 JSON 对象，字段：",
        '{"reply": "你的回复文本（直接回答玩家，必填）", "action": null | {"type": "override|inject_memory|access_rejudge|set_goal|retire", "payload": {"字段": "值"}}}',
        "reply：给玩家的戏外回复。",
        "action：只有当玩家明确要求执行幕后操作时才填；否则为 null。",
        "注意：payload 里的 npc_id / subject 用角色中文名（与在场名单/角色资料逐字一致）。",
        "输出字段只有 reply 与 action 两个。",
    ]
    return "\n".join(parts)


def build_audit_work_order(world: WorldContent, ledger: Ledger, scene_id: str) -> str:
    """Audit work order: settle world side effects from the prose.

    The audit infers time advance, location, presence, privacy and one-shot
    vs registered scenes from the narrative, then judges goal completion
    (M14 剧情目标) and lifecycle.
    """
    scene_id = scene_id or ledger.current_scene()
    player_name = world.player_name()
    scene = next((s for s in world.scenes if s.id == scene_id), None)
    present_ids = ledger.present_at(scene_id)
    names = [pid for pid in present_ids]
    scene_list = "、".join(s.id for s in world.scenes)
    return "\n".join(
        [
            "你是 AIWorld 的世界审计：玩家采纳一条正文后，你从正文里结算世界的副作用，并判定剧情目标与生命周期。",
            "只返回 JSON，格式如下：",
            '{"location": "场景id", "scene_name": "地点名（新地点给中文名）", "register_scene": false,'
            f' "participants": ["{player_name}", "朱明"], "private": false, "delta_minutes": 0,'
            ' "npc_moves": [{"npc_id": "朱明", "location": "朱明家"}],'
            ' "clock_to": "",'
            ' "completed_goal_ids": ["达成目标id"], "lifecycle": [{"npc_id": "朱明", "status": "retired"}]}',
            "判定规则：",
            "- location：正文里玩家此刻所在之处。玩家在正文中明确移动（离开/去别处/回家）时更新，否则保持当前场景。",
            "- participants（与 location 联动，二选一）：",
            "  · 玩家**没移动**：以工作单给出的在场名单为**默认基线**——正文没有明确的进出场就原样继承整份名单；"
            "只有正文明确写出某人离开（走掉/告辞）才移除，明确写出新人到场并需记入史实才添加。"
            "正文用\"她/他\"等代词指代的在场者视为仍在场（代词指代不清时保守保留原名单）；"
            "摊主、路人等叙事背景人物不进名单——他们只是舞台布景，不是这段史实的参与者。"
            "location 与 participants 都以下方给出的当前状态为基线。",
            "  · 玩家**移动了**：留在原地的人不进名单——他们的位置自然停在原地；"
            "正文里在**新场景出现并互动**的角色——无论同行、被玩家找到、还是主动搭话——都计入 participants"
            "（他们亲身参与了这段史实）。",
            "- npc_moves：正文明确写出某个 NPC **离开去了别处**（回家/回店/告辞离去）并写明去向时输出，"
            "每项 {\"npc_id\": \"...（角色中文名）\", \"location\": \"去向场景名（新地点给中文名）\"}。"
            "只写了\"走了\"没写去哪 → 该人不输出，位置保持原样；"
            "玩家自己的移动由 location 覆盖——这项只记 NPC。多数回合为空数组。",
            "- 场景若已在场景表里，用其中文名（见已注册场景清单）；正文进入未注册的新地点时，location 给一个中文名，scene_name 同名给出。"
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
            "- clock_to：正文**明确说了故事时间走到了几点**（\"到了晚上八点\"\"已经是第二天早上七点\"）时，"
            "输出该绝对时间，格式 YYYY-MM-DDTHH:MM:SS（日期默认跟当前时间走，正文说了跨天/某天才改）；"
            "给了 clock_to 就不用再估 delta_minutes。正文没明确说到点 → 留空字符串。",
            "- completed_goal_ids：正文已达到目标文本所述（小目标=当事达成；大目标=关键真相/冲突已解决）。"
            "只推进未达成的不填——推进由编剧纪律负责，审计只判终点。"
            "注意：目标归属者为 NPC 时，该目标 NPC 提及/推进目标只是推进（如王蓉提起接货），"
            "只有当正文里目标所述之事真正发生（如玩家答应了）才判完成——推进不算完成。"
            "大目标是章节：其下「子目标 x/y 已完成」只作参考证据、不是判据——"
            "正文若给出明确的关键了结（即使子目标没走完，如玩家绕道达成）即可判完成；"
            "反之，只是把子目标推了推、关键冲突没解决，就不判。",
            "- lifecycle：按正文语义识别角色退场（死亡/永久离开）→ retired。",
            "当前时间：" + (ledger.save.clock or "-"),
            "当前场景：" + (scene.id if scene else scene_id),
            "在场：" + ("、".join(names) or "暂无"),
            "已注册场景：location 用下列中文名（id 即中文名）；未注册的新地点同样给中文名（与场景表风格一致）：" + (scene_list or "（无）"),
            "活动目标：",
            *audit_goals_lines(ledger),
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
        scene_id = scene_id or ledger.current_scene()
        preset = preset or world.presets
        present_ids = ledger.present_at(scene_id)

        parts: list[str] = []
        # A 身份与任务
        parts += writer_identity()
        # ── 三级写作纪律：二级 → 三级 → 一级 连成完整梯队（2026-09-11 用户调整）
        # 二级 · 情节合理性（信息边界 / 抉择归属 / 设定一致 / 连续性 / 不出戏）
        # roster = 在场 ∩ 配 Actor：本轮谁可以被上缴深抉择（零 LLM 的配置量）。
        # 编剧据此决定该不该为某个角色的抉择停笔，引擎事后也按同一份名单裁决。
        # 主角恒不在名单里：他的抉择只能由玩家给（2026-09-13 主角入人物表后显式排除，
        # 不再依赖"主角卡没勾 Actor"这一约定）。
        roster = [
            pid
            for pid in present_ids
            if pid != world.player_name()
            and (npc := world.npcs.get(pid)) is not None
            and npc.has_actor
        ]
        parts += writer_story_rules(roster)
        # 三级 · 文风与剧情倾向（预设；默认遵循，可灵活）
        parts += writer_style_block(preset)
        # 一级 · 输出格式（全表唯一的硬边界；context 的人称约定随字段定义在此，
        # 2026-09-13 由二级迁入——避免二级先引用了尚未定义的 context）
        parts += writer_output_format(player_display_name(ledger))
        # ── 资料区：以下与事件日志同性质，都是写作取材，一律不标规则等级 ──
        # 世界概要（常驻，永不裁剪）
        parts += world_summary_block(world)
        # 世界书命中（本场相关背景：常驻 + 归属在场者 + 关键词触发）
        parts += active_lore_block(ledger, present_ids)
        # 玩家资料 + 在场 NPC（含 id 标注，编剧须用规范 id）
        parts += character_block(world, ledger, present_ids, include_ids=True)
        # 幕后注（机密，与二级「信息边界」呼应双保险）
        parts += private_notes_block(world, ledger, present_ids)
        # 场景快照（此刻环境：当前时间/在场/场景/可感知）
        parts += scene_snapshot_block(world, ledger, scene_id)
        # 在场 NPC 已知集（机械知识边界，取代旧近况块）
        parts += known_set_block(world, ledger, present_ids, scene_id)
        # 剧情目标（写作引导，贴近动笔位置）
        parts += goals_block(ledger)
        # 事件日志（更早摘要在前 → 最近原文在后，收尾紧贴玩家输入以便续写）
        parts += event_log_block(world, ledger)
        return "\n".join(parts)

    if viewer.startswith("actor_"):
        npc_id = viewer[len("actor_"):]
        return build_actor_work_order(
            world, ledger, npc_id, scene_id or ledger.current_scene()
        )

    raise ValueError(f"unknown viewer: {viewer}")