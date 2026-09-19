"""落卡窗口 2.0（2026-09-19）：新场景与人物进同一个窗口。

场景此前是**审计单方面静默落卡**的：``register_scene=True`` 就直接写
``scenes.json``，而描述只能是占位句（``SCENE_PLACEHOLDER``）——那句还每轮都被
注入。资产层凭空多一个没描述的场景、且没人被通知去补。

现在审计只把建议记进候选册（``save.locations``），写资产的唯一入口是
``POST /sessions/{sid}/locations/{name}/file`` —— 与人物侧同一条纪律：
**引擎只给候选（键），卡必须玩家落**。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

from app.core.workorder import build_audit_work_order
from app.ledger.save import LocationCandidate
from app.rules.movement import resolve_destination
from app.workers.landing import EVIDENCE_LIMIT, draft_npc_card, draft_scene, evidence_of
from app.world.models import SCENE_PLACEHOLDER
from tests.test_turn import PrefixKeyLLM, _runner


def _audit_scene(name: str, alias: str = "") -> dict:
    """审计：建议把 name 落卡（register_scene=True）。"""
    return {
        "location": name,
        "scene_name": alias,
        "register_scene": True,
        "participants": ["刘星"],
        "private": False,
        "delta_minutes": 0,
        "lifecycle": [],
    }


def _adopt_scene(session, settings, name: str, alias: str = "", *, text: str = "") -> None:
    """跑一轮（正文→QC→审计）并采纳，让审计把 name 建议进候选册。"""
    prose = text or f"你走到了{name}，四下看了看。"
    llm = PrefixKeyLLM(
        {
            "玩家输入：": {"prose": prose, "summary": f"刘星到了{name}。", "actor_questions": []},
            "正文：": {"status": "pass", "prose": prose, "issues": []},
            "已采纳正文": _audit_scene(name, alias),
        }
    )
    runner = _runner(session, llm, settings)
    candidate = asyncio.run(runner.run_turn(text or f"去{name}"))
    asyncio.run(runner.adopt(candidate.candidate_id))


def _append_narrative(ledger, *, location: str, participants: list[str], body: str) -> None:
    """直接往账本里补一条正文（草稿 / 证据只读 by_participant / by_location 索引）。"""
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": ledger.save.clock,
            "location": location,
            "participants": participants,
            "known_by": None,
            "body": body,
            "summary": body[:20],
            "source": "turn",
        }
    )


@contextmanager
def _api(tmp_path):
    """开档 + 起 API（落卡要过 check_assets + save_world_assets，得走真实的写路径）。"""
    from tests.test_api import _make_client

    with _make_client(tmp_path) as client:
        sid = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()["sid"]
        yield client, sid, client.app.state.sessions[sid]


# --------------------------------------------------------------------------
# 审计不再静默注册
# --------------------------------------------------------------------------

def test_scene_hint_never_writes_assets(session, settings, world_root) -> None:
    """建议落卡 = 只入候选册；scenes.json 一动都不动。"""
    before = json.loads((world_root / "scenes.json").read_text(encoding="utf-8"))
    _adopt_scene(session, settings, "街角奶茶店")

    assert session.ledger.pending_locations() == ["街角奶茶店"]
    assert not any(s.id == "街角奶茶店" for s in session.world.scenes)  # 内存世界也没多
    after = json.loads((world_root / "scenes.json").read_text(encoding="utf-8"))
    assert after == before  # 资产层零改动（本轮的核心回归点）
    assert session.ledger.save.player_scene == "街角奶茶店"  # 事件位置照旧


def test_audit_prompt_lists_pending_and_dismissed(session, settings) -> None:
    """审计得知道候选册里有什么，否则它会自造变体名、把册子越攒越多条。"""
    _adopt_scene(session, settings, "村东苇塘", alias="苇塘")
    _adopt_scene(session, settings, "城南破庙")
    session.ledger.save.locations["城南破庙"].status = "dismissed"

    order = build_audit_work_order(
        session.world, session.ledger, session.ledger.current_scene()
    )
    assert "待落卡的地点" in order and "村东苇塘" in order
    assert "已定为临时的地点" in order and "城南破庙" in order
    assert "register_scene 是**建议落卡**" in order


# --------------------------------------------------------------------------
# 解析域：待落卡 / 已定为临时的地方也得认得出来
# --------------------------------------------------------------------------

def test_pending_scene_resolves_in_rules_navigation(session, settings) -> None:
    """规则寻路要认得待落卡地点——否则"去 X"退化成交给审计，等于把决定权让给 LLM。"""
    _adopt_scene(session, settings, "村东苇塘", alias="苇塘")
    assert resolve_destination("去村东苇塘", session.world, session.ledger) == "村东苇塘"
    assert resolve_destination("去苇塘", session.world, session.ledger) == "村东苇塘"


def test_dismissed_scene_is_sticky_but_still_resolvable(session, settings) -> None:
    """× 是玩家的裁决：审计再建议也只攒别名，不改回 pending（与人物侧不对称）。"""
    _adopt_scene(session, settings, "城南破庙")
    session.ledger.save.locations["城南破庙"].status = "dismissed"

    _adopt_scene(
        session, settings, "城南破庙", alias="河边的废寺", text="你走进河边那座废寺里。"
    )

    assert session.ledger.pending_locations() == []  # 不复活
    assert session.ledger.save.locations["城南破庙"].status == "dismissed"
    assert session.ledger.save.locations["城南破庙"].aliases == ["河边的废寺"]
    # 但解析域照旧认得它（别名不丢）
    assert resolve_destination("去城南破庙", session.world, session.ledger) == "城南破庙"


def test_reset_clears_the_location_book_but_not_the_scene_table(tmp_path) -> None:
    """候选册是运行态、随世界重置清零；场景表是资产、不动。"""
    with _api(tmp_path) as (client, sid, live):
        live.ledger.save.locations["村东苇塘"] = LocationCandidate(aliases=["苇塘"])
        live.ledger.persist_save()
        before = json.loads((live.world_dir / "scenes.json").read_text(encoding="utf-8"))

        assert client.post(f"/api/sessions/{sid}/reset").status_code == 200

        assert live.ledger.save.locations == {}
        after = json.loads((live.world_dir / "scenes.json").read_text(encoding="utf-8"))
        assert after == before


# --------------------------------------------------------------------------
# 落卡 / 放弃（API，走真实写路径）
# --------------------------------------------------------------------------

def test_scene_landing_writes_assets_and_clears_the_book(tmp_path) -> None:
    with _api(tmp_path) as (client, sid, live):
        live.ledger.save.locations["村东苇塘"] = LocationCandidate(aliases=["苇塘"])

        resp = client.post(
            f"/api/sessions/{sid}/locations/村东苇塘/file",
            json={"perceivable": "一片苇子围着的浅塘，风过时哗哗响。"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        scenes = json.loads((live.world_dir / "scenes.json").read_text(encoding="utf-8"))
        landed = next(s for s in scenes if s["id"] == "村东苇塘")
        assert landed["perceivable"] == "一片苇子围着的浅塘，风过时哗哗响。"
        assert landed["aliases"] == ["村东苇塘", "苇塘"]  # 变体名一并落表
        assert landed["region"] == ""  # 留空 = 全域公共区（落卡前是 adhoc 哨兵）
        # 键交还给资产：候选册里没有他了，界面也不再列
        assert "村东苇塘" not in live.ledger.save.locations
        assert client.get(f"/api/sessions/{sid}/state").json()["unfiled_scenes"] == []
        # 内存世界也认得了（reload_world 生效）
        assert any(s.id == "村东苇塘" for s in live.world.scenes)


def test_scene_landing_keeps_placeholder_when_description_empty(tmp_path) -> None:
    """描述留空 → 占位句；与旧行为一致，区别是这次是玩家看着空栏点的确定。"""
    with _api(tmp_path) as (client, sid, live):
        live.ledger.save.locations["街角奶茶店"] = LocationCandidate()

        assert (
            client.post(f"/api/sessions/{sid}/locations/街角奶茶店/file", json={}).status_code
            == 200
        )

        scenes = json.loads((live.world_dir / "scenes.json").read_text(encoding="utf-8"))
        landed = next(s for s in scenes if s["id"] == "街角奶茶店")
        assert landed["perceivable"] == SCENE_PLACEHOLDER


def test_scene_endpoints_reject_names_not_in_the_book(tmp_path) -> None:
    with _api(tmp_path) as (client, _sid, _live):
        assert client.get("/api/sessions/x/locations/不存在/evidence").status_code == 404
        assert client.post("/api/sessions/x/locations/不存在/file", json={}).status_code == 404


def test_scene_discard_marks_dismissed(tmp_path) -> None:
    with _api(tmp_path) as (client, sid, live):
        live.ledger.save.locations["城南破庙"] = LocationCandidate(aliases=["河边的废寺"])

        assert (
            client.post(f"/api/sessions/{sid}/locations/城南破庙/discard").status_code == 200
        )

        assert live.ledger.save.locations["城南破庙"].status == "dismissed"
        assert live.ledger.pending_locations() == []
        # 别名留在册里：解析域照旧认得它
        assert live.ledger.location_aliases()["河边的废寺"] == "城南破庙"
        # 但没进场景表（× = 定为临时，不是落卡）
        assert not any(s.id == "城南破庙" for s in live.world.scenes)


# --------------------------------------------------------------------------
# 草稿：只喂该主体自己的原文
# --------------------------------------------------------------------------

def test_evidence_of_takes_the_last_few_and_skips_empty(session) -> None:
    led = session.ledger
    _append_narrative(led, location="酒馆", participants=["刘星", "卢克"], body="卢克擦着杯子。")
    _append_narrative(led, location="酒馆", participants=["刘星", "王二"], body="王二蹲在墙根啃饼。")
    for i in range(EVIDENCE_LIMIT + 3):
        _append_narrative(
            led, location="酒馆", participants=["刘星", "卢克"], body=f"第 {i} 次攀谈。"
        )
    led.append({"id": led.allocate_event_id(), "kind": "state", "at": led.save.clock, "location": "酒馆", "participants": ["卢克"], "body": "", "summary": "记账事件"})

    picked = evidence_of(led.by_participant["卢克"])
    assert len(picked) == EVIDENCE_LIMIT  # 只取最近几条
    assert all(p["body"] for p in picked)  # 空的（占位 / 记账）不进
    assert all("王二" not in p["body"] for p in picked)


def test_npc_draft_feeds_only_his_own_prose(session, settings) -> None:
    led = session.ledger
    _append_narrative(led, location="酒馆", participants=["刘星", "卢克"], body="卢克擦着杯子，说要请客。")
    _append_narrative(led, location="主街", participants=["刘星", "王二"], body="王二蹲在墙根啃饼。")

    llm = PrefixKeyLLM({"角色：": {"appearance": "粗布短打，肩搭汗巾。", "persona": "话多，爱打听。"}})
    draft = asyncio.run(draft_npc_card(llm, led, "卢克", model="cheap-x"))

    assert draft["appearance"] == "粗布短打，肩搭汗巾。"
    assert draft["persona"] == "话多，爱打听。"
    assert draft["evidence_count"] == 1

    prompt = "\n".join(m["content"] for m in llm.calls[0]["messages"])
    assert "卢克擦着杯子" in prompt
    assert "王二" not in prompt  # 不喂别人的原文
    assert llm.calls[0]["model"] == "cheap-x"  # 走 model_cheap
    assert "不许推断" in prompt and "留空串" in prompt  # 硬性纪律在场


def test_scene_draft_feeds_only_prose_of_that_place(session) -> None:
    led = session.ledger
    _append_narrative(led, location="村东苇塘", participants=["刘星"], body="苇子齐腰高，水面上有蜻蜓。")
    _append_narrative(led, location="主街", participants=["刘星"], body="街上人声嘈杂。")

    llm = PrefixKeyLLM({"地点：": {"perceivable": "齐腰的苇子围着浅塘，风过时哗哗响。"}})
    draft = asyncio.run(draft_scene(llm, led, "村东苇塘"))

    assert draft["evidence_count"] == 1
    prompt = "\n".join(m["content"] for m in llm.calls[0]["messages"])
    assert "苇子齐腰高" in prompt
    assert "人声嘈杂" not in prompt  # 别处的正文不喂
    assert "绝对不写" in prompt and "历史事件" in prompt  # 感知 vs 情节 那条约束在场


def test_draft_without_evidence_does_not_call_the_llm(session) -> None:
    """没证据就不生成、也不花钱——空栏是"还没写到"。"""
    llm = PrefixKeyLLM({"*": {"appearance": "不该被写出来", "persona": "不该被写出来"}})
    draft = asyncio.run(draft_npc_card(llm, session.ledger, "从没出现过的人"))
    assert llm.calls == []
    assert draft == {"appearance": "", "persona": "", "evidence_count": 0}
