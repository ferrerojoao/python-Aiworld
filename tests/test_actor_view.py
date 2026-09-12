"""Actor 视图、在场名单与三级分层（2026-09-11 人称与记忆口径修正）。

覆盖：玩家进在场名单且 Actor 视图剔掉自己；写手侧同样有玩家；占位名「你」
降级为「玩家」、真名标注「名（玩家）」；context 人称一律写主角名（未设定时
退化「玩家」），writer / actor 两侧契约同口径；known_set 含全域公开事件（王蓉
0 条亲历但有「开场」）；memory_limit=0 不出记忆块；clean_context 不改人称；
写手工作单的三级梯队顺序（二级 → 三级 → 一级，整体前置于资料区）与抬头不带解释句。
"""

from __future__ import annotations

from app.core.workorder import (
    actor_contract,
    build_actor_work_order,
    build_work_order,
    scene_snapshot_block,
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


def _presence_line(text: str) -> str:
    """取场景快照块的在场行（写手工作单里另有「在场 NPC：」行，须区分）。"""
    return next(line for line in text.splitlines() if line.startswith("在场（"))


def test_actor_view_lists_player_and_excludes_self(session):
    """在场名单必须含玩家（对话对象），且不言 Actor 自己。"""
    ledger = session.ledger
    _narrative(ledger, location="主街", participants=["player", "朱明"], body="两人在街上碰面。", summary="碰面")

    order = build_actor_work_order(session.world, ledger, "朱明", "主街")
    line = _presence_line(order)
    assert "玩家" in line
    assert "朱明" not in line


def test_writer_view_also_lists_player(session):
    """写手侧走同一个快照块，玩家同样在名单里。"""
    ledger = session.ledger
    _narrative(ledger, location="主街", participants=["player", "朱明"], body="两人在街上碰面。", summary="碰面")

    order = build_work_order("writer", session.world, ledger, "主街")
    assert "玩家" in _presence_line(order)


def test_player_name_placeholder_falls_back_to_player(session):
    """主角名是占位符「你」时只写「玩家」，避免与「你 = 你自己」打架。"""
    ledger = session.ledger

    ledger.save.player.name = "你"
    line = _presence_line("\n".join(scene_snapshot_block(session.world, ledger, "主街")))
    assert "玩家" in line
    assert "你" not in line

    ledger.save.player.name = "刘星"
    line = _presence_line("\n".join(scene_snapshot_block(session.world, ledger, "主街")))
    assert "刘星（玩家）" in line


def test_known_set_includes_global_public_event(session):
    """王蓉 0 条亲历，但全域公开的「开场」应进她的已知集（第三来源）。"""
    ledger = session.ledger
    assert ledger.by_participant.get("王蓉", []) == []
    mem = ledger.known_set("王蓉", "主街")
    assert any("开场" in line for line in mem)


def test_memory_limit_zero_omits_memory_block(session):
    """memory_limit=0 = 不开记忆：条目块整块消失（[-0:] 守卫）。"""
    ledger = session.ledger
    _narrative(ledger, location="主街", participants=["player", "朱明"], body="两人在街上碰面。", summary="碰面")
    session.world.meta.memory_limit = 0

    order = build_actor_work_order(session.world, ledger, "朱明", "主街")
    assert "你知道的事：" not in order
    assert ledger.known_set("朱明", "主街", limit=0) == []


def test_clean_context_does_not_rewrite_pronouns():
    """人称一律原样保留——只有 strip 是允许的动作。"""
    raw = "你包了一夜刚被刘星拍醒，是你先跟刘星提的。"
    assert clean_context(raw) == raw
    assert clean_context("  两头有空格  ") == "两头有空格"


def test_writer_contract_pins_context_pronouns():
    """编剧契约必须把 context 的人称定死（只约束该字段，正文照旧）：
    以该 NPC 为「你」，提到玩家写其**姓名**、不写「玩家」（2026-09-11 改口径）。"""
    rules = "\n".join(writer_story_rules("刘星"))
    assert "context 的人称约定" in rules
    assert "以该 NPC 为「你」" in rules
    assert "一律写玩家姓名「刘星」" in rules
    assert "不要写成「玩家」" in rules

    # 主角名未设定（占位符「你」）时退化为「玩家」，不出现自相矛盾的措辞。
    fallback = "\n".join(writer_story_rules("玩家"))
    assert "一律写「玩家」" in fallback
    assert "不要写成「玩家」" not in fallback


def test_writer_prompt_is_tiered(session):
    """三级分层（2026-09-11）：二级 → 三级 → 一级 连成梯形排在身份之后、
    资料区之前；抬头只留等级名、不带解释句。世界概要降级为资料、不标等级。"""
    session.world.presets.writer_guidelines = "文风偏好：白描为主，少形容词。"
    session.ledger.save.player.name = "刘星"
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
        participants=["player", "朱明", "王蓉"],
        body="三人在网吧碰头。",
        summary="网吧聚齐",
    )
    order = build_work_order("writer", session.world, ledger, "网吧")
    assert "本轮可上缴深抉择的角色：朱明" in order
    assert "其余角色一律由你直接决定并写进正文" in order


def test_actor_contract_explains_pronouns():
    """Actor 契约必须讲明人称读法与归属冲突的判据。玩家一律用主角名指代
    （2026-09-11 改口径）；主角名未设定时退化为「玩家」。"""
    contract = "\n".join(actor_contract("朱明", "刘星"))
    assert "人称读法" in contract
    assert "「你」= 你自己（朱明）" in contract
    assert "「刘星」= 你的对话对象" in contract
    assert "不要替刘星做决定" in contract
    assert "以「你知道的事」清单为准" in contract

    # 占位名回退：主角名未设定时仍是「玩家」。
    fallback = "\n".join(actor_contract("朱明"))
    assert "「玩家」= 你的对话对象" in fallback


def test_actor_work_order_injects_player_name(session):
    """Actor 工作单里的对话对象必须写成主角真名（不再写「玩家」）。"""
    session.ledger.save.player.name = "刘星"
    order = build_actor_work_order(session.world, session.ledger, "朱明", "主街")
    assert "「刘星」= 你的对话对象" in order
    assert "不要替刘星做决定" in order
    # 情境人称同口径：编剧侧拿到的也是主角名。
    assert "一律写玩家姓名「刘星」" not in order  # 该条属编剧契约，不进 Actor 工作单
