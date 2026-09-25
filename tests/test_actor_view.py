"""Actor 视图、在场名单与三级分层（2026-09-11 人称与记忆口径修正）。

覆盖：玩家进在场名单且 Actor 视图剔掉自己；写手侧同样有玩家；占位名「你」
降级为「玩家」、真名标注「名（玩家）」；context 人称一律写主角名（未设定时
退化「玩家」），writer / actor 两侧契约同口径；known_set 的第三来源只在**显式**
全域公共（场景 region = `全域`）时人人可闻、留空则谁也不闻（王蓉 0 条亲历，靠
「开场」验证）；known_set 的 0 = 全部；clean_context 不改人称；
写手工作单的三级梯队顺序（二级 → 三级 → 一级，整体前置于资料区）与抬头不带解释句。
"""

from __future__ import annotations

from app.core.workorder import (
    actor_contract,
    build_actor_work_order,
    build_work_order,
    scene_snapshot_block,
    writer_output_format,
    writer_story_rules,
)
from app.workers.actor import clean_context


def _narrative(ledger, *, location, participants, body, summary, at="2026-07-14T09:00:00"):
    """写一条叙述事件（确定在场名单用）。"""
    event = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": at,
        "location": location,
        "participants": participants,
        "known_by": None,
        "body": body,
        "summary": summary,
        "player_input": None,
        "source": "test",
    }
    ledger.append(event)
    return event


def _rename_player(world, new_id: str) -> None:
    """改主角名 = 换人物表键（主角就是表里 is_player 的那张卡）。"""
    card = world.player()
    world.npcs.pop(card.id, None)
    card.id = new_id
    world.npcs[new_id] = card


def _presence_line(text: str) -> str:
    """取场景快照块的在场行（写手工作单里另有「在场 NPC：」行，须区分）。"""
    return next(line for line in text.splitlines() if line.startswith("在场（"))


def test_actor_view_lists_player_and_excludes_self(session):
    """在场名单必须含主角（对话对象）并标注（玩家），且不言 Actor 自己。"""
    ledger = session.ledger
    _narrative(ledger, location="主街", participants=["刘星", "朱明"], body="两人在街上碰面。", summary="碰面")

    order = build_actor_work_order(session.world, ledger, "朱明", "主街")
    line = _presence_line(order)
    assert "刘星（玩家）" in line
    assert "朱明" not in line


def test_writer_view_also_lists_player(session):
    """写手侧走同一个快照块，主角同样在名单里。"""
    ledger = session.ledger
    _narrative(ledger, location="主街", participants=["刘星", "朱明"], body="两人在街上碰面。", summary="碰面")

    order = build_work_order("writer", session.world, ledger, "主街")
    assert "刘星（玩家）" in _presence_line(order)


def test_player_name_placeholder_falls_back_to_player(session):
    """主角名是占位符「你」时只写「玩家」，避免与「你 = 你自己」打架。"""
    world = session.world
    ledger = session.ledger

    _rename_player(world, "你")
    line = _presence_line("\n".join(scene_snapshot_block(world, ledger, "主街")))
    assert "玩家" in line

    _rename_player(world, "刘星")
    line = _presence_line("\n".join(scene_snapshot_block(world, ledger, "主街")))
    assert "刘星（玩家）" in line


def test_known_set_includes_global_public_event(session):
    """王蓉 0 条亲历：**显式标了全域公共**（场景 region = `全域`）的「开场」进她的已知集
    （第三来源）；而 region **留空 = 不传播** ⇒ 同一条公开事件她一点也闻不到。

    2026-09-24 **默认值反转**：旧口径"空 region = 全域公共"下这条不必标 `全域`。把
    "是不是人人可闻"从一个默认值改成一次**显式表态**，两个半边都得钉住——只钉
    正方向的话，"留空"悄悄变回公共也没人发现。
    """
    ledger = session.ledger
    assert ledger.by_participant.get("王蓉", []) == []

    assert not any("开场" in line for line in ledger.known_set("王蓉", "主街", 0)), "留空 = 不传播，谁也不风闻"

    for s in ledger.world.scenes:
        s.region = "全域"
    mem = ledger.known_set("王蓉", "主街", 0)
    assert any("开场" in line for line in mem), "显式标全域公共才人人可闻"


def test_known_set_zero_returns_everything(session):
    """0 = 全部（2026-09-16 反转旧口径"0 = 不给"）。

    旧语义与玩家直觉相反，也与事件日志的 0 不一致；用户实测反馈"NPC 总是
    忘记事"，正是因为上限太小——0 让玩家能一把放开。"""
    ledger = session.ledger
    for i in range(8):
        _narrative(
            ledger,
            location="主街",
            participants=["刘星", "朱明"],
            body=f"第{i}件事。",
            summary=f"第{i}件事",
        )
    everything = ledger.known_set("朱明", "主街", 0)
    assert all(f"第{i}件事" in "\n".join(everything) for i in range(8))  # 全部
    assert len(everything) >= 8  # 另有世界自带的「开场」等公开事件
    assert len(ledger.known_set("朱明", "主街", 3)) == 3  # 仍可截断


def test_clean_context_does_not_rewrite_pronouns():
    """人称一律原样保留——只有 strip 是允许的动作。"""
    raw = "你包了一夜刚被刘星拍醒，是你先跟刘星提的。"
    assert clean_context(raw) == raw
    assert clean_context("  两头有空格  ") == "两头有空格"


def test_writer_contract_pins_context_pronouns():
    """context 的人称约定必须贴着字段定义（2026-09-11 定口径，2026-09-13 迁位）：
    以该 NPC 为「你」，提到玩家写其**姓名**、不写「玩家」。

    位置要求来自用户 2026-09-13 的观察：二级先于一级，若在二级写"context 的
    人称约定"，读到那行时 context 还没被定义（前向引用）。字段写法属格式层，
    随 actor_questions 一起放在一级。
    """
    fmt = "\n".join(writer_output_format("刘星"))
    assert "· context：" in fmt
    assert "以该 NPC 为「你」" in fmt
    assert "一律写玩家姓名「刘星」" in fmt
    assert "一句只用一个人称" in fmt
    assert "只约束这个字段" in fmt

    # 二级不再提前展开字段写法，只留机制并指向一级。
    rules = "\n".join(writer_story_rules(["朱明"]))
    assert "把这一拍写到抉择点为止" in rules
    assert "见「一级 · 输出格式」" in rules
    assert "context 的人称约定" not in rules
    assert "不要写成" not in rules

    # 主角名未设定（占位符「你」）时退化为「玩家」，不出现自相矛盾的措辞。
    fallback = "\n".join(writer_output_format("玩家"))
    assert "一律写「玩家」" in fallback
    assert "不要写成" not in fallback


def test_context_rule_appears_after_field_definition(session):
    """结构性回归：工作单里 context 的写法规则必须排在字段定义之后
    （字段定义在 JSON 模板的 actor_questions 里），不再有前向引用。"""
    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert '"actor_questions": [{"npc_id"' in order  # 字段定义
    assert order.index('"actor_questions": [{"npc_id"') < order.index("· context：")
    # 二级里那份"提前讲写法"的旧文案已迁走，全文只出现一次 context 人称约定。
    assert order.count("以该 NPC 为「你」") == 1


def test_writer_prompt_is_tiered(session):
    """三级分层（2026-09-11）：二级 → 三级 → 一级 连成梯形排在身份之后、
    资料区之前；抬头只留等级名、不带解释句。世界概要降级为资料、不标等级。"""
    session.world.presets.writer_guidelines = "文风偏好：白描为主，少形容词。"
    order = build_work_order("writer", session.world, session.ledger, "网吧")

    # 三级标记齐全，顺序为 二级 < 三级 < 一级，且整体在资料区之前（不切开资料区）。
    assert "【二级 · 情节合理性】" in order
    assert "【三级 · 文风与剧情倾向】" in order
    assert "【一级 · 输出格式】" in order
    assert order.index("【二级") < order.index("【三级") < order.index("【一级")
    assert order.index("【一级") < order.index("玩家资料：")

    # 抬头只留等级名：不带自我说明句。
    assert "最硬的一级" not in order
    assert "不是硬边界" not in order
    assert "重掷或改写" not in order
    assert "【二级 · 情节合理性】\n" in order
    assert "【一级 · 输出格式】\n" in order

    # 世界概要不再挂硬规则标签（退化为与事件日志同级的资料）。
    assert "世界概要：" in order
    assert "世界概要（不可违背）" not in order
    assert "金科玉律" not in order


def test_style_sample_injected_with_adjacent_ban(session):
    """文风示范（2026-09-11）：三级块内紧贴准则注入，只留「文风示范：」
    标记 + 原文（贴身禁令经用户要求已去掉，先试无禁令版）；空 = 不注入。"""
    session.world.presets.writer_guidelines = "文风偏好：白描为主。"
    session.world.presets.style_sample = "他把伞收了，雪就落满了肩。"

    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "文风示范：" in order
    assert "「他把伞收了，雪就落满了肩。」" in order
    # 禁令已去掉（用户 23:08 要求）。
    assert "不得以任何形式出现在正文里" not in order
    # 示范跟在准则之后（都在三级抬头与一级之间）。
    assert order.index("文风示范") < order.index("【一级 · 输出格式】")
    assert order.index("文风偏好：白描为主。") < order.index("文风示范")

    # 留空 = 不注入（连"文风示范"字样都不出现）。
    session.world.presets.style_sample = ""
    empty = build_work_order("writer", session.world, session.ledger, "网吧")
    assert "文风示范" not in empty


def test_style_sample_is_injected_in_full(session):
    """范文**整段进提示词，不许截断**（2026-09-25 用户要求去掉字数限制）。

    上一版说明写着「100~200 字」，容易让人以为引擎只取一小段；实际上 `writer_style_block`
    是原样拼进去的，这一级又不在可裁块里（裁的只有世界书命中）。这条把"贴多长就进多长"
    钉死——将来谁加了切片或长度上限，这里必红。
    """
    sample = "".join(f"第{i}句白描，雪落在肩上。" for i in range(120))  # 约 1700 字
    session.world.presets.style_sample = sample

    order = build_work_order("writer", session.world, session.ledger, "网吧")
    assert f"「{sample}」" in order, "范文必须原样整段进三级块，不能被截断"


def test_writer_roster_clause_follows_presence_and_tickets(session):
    """本轮可上缴名单 = 在场 ∩ 配 Actor，必须显式下发（2026-09-11）：
    编剧不再从各人名后的括号自行推断谁不能上缴——推错一格就会上缴无票角色，
    而引擎按票丢弃，那一拍永远是空的。"""
    ledger = session.ledger

    # 无人在场：名单为空 → 明说"无人可上缴"，所有抉择编剧自己写。
    order = build_work_order("writer", session.world, ledger, "网吧")
    assert "本轮没有任何角色可上缴深抉择" in order

    # 朱明（有票）在场 → 名单只列他；无票的王蓉即使同场也不列。
    _narrative(
        ledger,
        location="网吧",
        participants=["刘星", "朱明", "王蓉"],
        body="三人在网吧碰头。",
        summary="网吧聚齐",
    )
    order = build_work_order("writer", session.world, ledger, "网吧")
    assert "本轮可上缴深抉择的角色：朱明" in order
    assert "其余你直接写" in order  # 名单外的人不逐一点名（2026-09-15 压缩）


def test_actor_contract_explains_pronouns():
    """Actor 契约必须讲明人称读法与归属冲突的判据。玩家一律用主角名指代
    （2026-09-11 改口径）；主角名未设定时退化为「玩家」。"""
    contract = "\n".join(actor_contract("朱明", "刘星"))
    assert "人称读法" in contract
    assert "「你」= 你自己（朱明）" in contract
    assert "「刘星」= 你的对话对象" in contract
    # 2026-09-12 去否定化：改为正向陈述（决定的归属在本人）。
    assert "刘星的抉择留给本人" in contract
    assert "不要替刘星做决定" not in contract
    assert "以「你知道的事」为准" in contract  # 归属冲突的判据（2026-09-15 压缩后仍逐字保留）

    # 占位名回退：主角名未设定时仍是「玩家」。
    fallback = "\n".join(actor_contract("朱明"))
    assert "「玩家」= 你的对话对象" in fallback


def test_actor_work_order_injects_player_name(session):
    """Actor 工作单里的对话对象必须写成主角真名（不再写「玩家」）。"""
    order = build_actor_work_order(session.world, session.ledger, "朱明", "主街")
    assert "「刘星」= 你的对话对象" in order
    assert "刘星的抉择留给本人" in order
    # 情境人称同口径：编剧侧拿到的也是主角名。
    assert "一律写玩家姓名「刘星」" not in order  # 该条属编剧契约，不进 Actor 工作单
