"""大总结（2026-10-10）：压缩事件流、保住位置地基、滚动多次。

每条用例都对应一条**非做不可**的理由（见 app/ledger/digest.py 的三条纪律），
不是凭空写的覆盖率。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.ledger.digest import (
    apply_digest,
    digest_overview,
    digest_source,
    location_rebuild_events,
    pending_events,
)
from app.ledger.events import SOURCE_DIGEST, SOURCE_DIGEST_LOCATION
from app.main import create_app
from app.runtime.session import open_session
from app.workers.digest import run_digest
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT


def _turn(
    session,
    *,
    at: str,
    location: str,
    participants: list[str],
    body: str = "",
    summary: str = "",
    known_by: list[str] | None = None,
) -> dict:
    """往事件流里塞一条叙事事件（正文走 body、记账事件走 summary）。"""
    event = {
        "id": session.ledger.allocate_event_id(),
        "kind": "narrative",
        "at": at,
        "location": location,
        "participants": participants,
        "known_by": known_by,
        "body": body,
        "summary": summary,
        "source": "turn",
    }
    session.ledger.append(event)
    return event


def _drain(session) -> None:
    """塞几条各色事件，凑出一段"历史"。"""
    player = session.world.player_name()
    _turn(session, at="2026-07-14T09:00:00", location="主街", participants=[player, "朱明"], body="朱明在网吧门口喊你。")
    _turn(session, at="2026-07-14T10:00:00", location="鱼市", participants=["朱明"], summary="朱明前往鱼市")
    _turn(session, at="2026-07-14T11:00:00", location="鱼市", participants=[player, "朱明", "王蓉"], body="鱼市上你碰见了王蓉。")


# ---------------------------------------------------------------------------
# 压缩本身
# ---------------------------------------------------------------------------


def test_digest_reduces_the_stream_to_one_summary_event(session) -> None:
    _drain(session)
    assert len(session.ledger.narratives) >= 4

    result = apply_digest(session.ledger, text="青石镇编年史", summary="一段往事")

    assert [ev["source"] for ev in session.ledger.narratives] == [SOURCE_DIGEST]
    digest_event = session.ledger.narratives[0]
    assert digest_event["body"] == "青石镇编年史"
    assert digest_event["summary"] == "一段往事"
    assert digest_event["known_by"] is None, "总结是公开的——它要能进编剧的日志"
    assert digest_event["location"] is None, "总结没有位置，别去污染 where_is"
    assert result["revision"] == 1


def test_apply_digest_rejects_empty_text(session) -> None:
    _drain(session)
    with pytest.raises(ValueError):
        apply_digest(session.ledger, text="   ")


def test_digest_summary_falls_back_to_body(session) -> None:
    _drain(session)
    apply_digest(session.ledger, text="青石镇这一年过得很快。", summary="")
    assert session.ledger.narratives[0]["summary"] == "青石镇这一年过得很快。"[:40]


# ---------------------------------------------------------------------------
# 位置地基：这是"清空事件流"真正的雷
# ---------------------------------------------------------------------------


def test_digest_keeps_everyone_present(session) -> None:
    """压缩后在场推导必须照旧——否则编剧不知道谁在、Actor 一个都不派。"""
    _drain(session)

    apply_digest(session.ledger, text="编年史")

    assert session.ledger.present_at("鱼市") == sorted(["刘星", "朱明", "王蓉"])
    assert session.ledger.present_at("主街") == []


def test_digest_preserves_last_seen_moment(session) -> None:
    """位置重建必须**保真 at**：统一写成"现在的钟"会让所有人的最后目击变"刚刚"。"""
    _drain(session)

    apply_digest(session.ledger, text="编年史")

    fact = session.ledger.where_is("朱明")
    assert fact["location"] == "鱼市"
    assert fact["at"] == "2026-07-14T11:00:00"


def test_rebuild_groups_by_location_and_moment(session) -> None:
    """同一地点、不同时刻必须分成两条——合一条就篡改其中一人的新鲜度。"""
    _turn(session, at="2026-07-14T09:00:00", location="鱼市", participants=["朱明"])
    _turn(session, at="2026-07-14T11:00:00", location="鱼市", participants=["王蓉"])

    by_key = {(ev["location"], ev["at"]): ev["participants"] for ev in location_rebuild_events(session.ledger)}

    assert by_key[("鱼市", "2026-07-14T09:00:00")] == ["朱明"]
    assert by_key[("鱼市", "2026-07-14T11:00:00")] == ["王蓉"]


def test_rebuild_skips_retired(session) -> None:
    """退场者的位置对引擎已无意义（present_at 会跳过他），别重建。"""
    from app.ledger.save import EntityRuntime

    _turn(session, at="2026-07-14T09:00:00", location="鱼市", participants=["朱明"])
    session.ledger.save.entities["朱明"] = EntityRuntime(lifecycle="retired")

    names = [pid for ev in location_rebuild_events(session.ledger) for pid in ev["participants"]]
    assert "朱明" not in names


def test_rebuild_ignores_blank_participants(session) -> None:
    """事件落盘**不做校验**：审计给的空名字会造出一条没有主体的位置事件。"""
    _turn(session, at="2026-07-14T09:00:00", location="鱼市", participants=["朱明", ""])

    names = [pid for ev in location_rebuild_events(session.ledger) for pid in ev["participants"]]

    assert "" not in names
    assert "朱明" in names


# ---------------------------------------------------------------------------
# 管线事件对玩家不可见
# ---------------------------------------------------------------------------


def test_plumbing_events_stay_out_of_the_log_and_memory(session) -> None:
    _drain(session)
    apply_digest(session.ledger, text="编年史")

    assert [ev["source"] for ev in session.ledger.access_view()] == [SOURCE_DIGEST]
    assert all(ev["source"] != SOURCE_DIGEST_LOCATION for ev in session.ledger.narratives)
    # 事件流里确实躺着它们（位置靠它们撑），只是不进对外视图。
    assert any(ev["source"] == SOURCE_DIGEST_LOCATION for ev in session.ledger.events)


# ---------------------------------------------------------------------------
# 不可逆 ⇒ 归档；以及引用完整性
# ---------------------------------------------------------------------------


def test_digest_archives_the_old_stream(session) -> None:
    _drain(session)
    events_path = Path(session.ledger.events_path)
    before = events_path.read_text(encoding="utf-8")

    result = apply_digest(session.ledger, text="编年史")

    archived = Path(session.ledger.save_dir) / "archive" / result["archived"]
    assert archived.exists(), "不可逆操作必须先归档"
    assert archived.read_text(encoding="utf-8") == before
    assert "鱼市上你碰见了王蓉" not in events_path.read_text(encoding="utf-8")


def test_digest_clears_dangling_access_overrides(session) -> None:
    event = _turn(session, at="2026-07-14T09:00:00", location="主街", participants=["刘星"])
    session.ledger.save.access_overrides[event["id"]] = ["朱明"]

    apply_digest(session.ledger, text="编年史")

    assert session.ledger.save.access_overrides == {}


def test_digest_does_not_rewind_event_ids(session) -> None:
    """事件号必须继续递增：归 1 会让新旧事件撞号，改判/目标按号引用全部错位。"""
    _drain(session)
    before = session.ledger.save.meta.next_event_id

    apply_digest(session.ledger, text="编年史")

    assert session.ledger.save.meta.next_event_id > before
    reopened = open_session(session.world_dir)
    assert reopened.ledger.save.meta.next_event_id > before
    assert len(reopened.ledger.present_at("鱼市")) == 3, "重新加载后位置地基仍在"


def test_digest_keeps_the_world_state_untouched(session) -> None:
    """压缩事件流不该让世界回退：钟、状态、目标一律不动。"""
    _drain(session)
    session.ledger.save.clock = "2026-07-20T20:00:00"
    from app.ledger.save import EntityRuntime, StateItem

    session.ledger.save.entities["朱明"] = EntityRuntime(states=[StateItem(text="左腿摔伤")])

    apply_digest(session.ledger, text="编年史")

    assert session.ledger.save.clock == "2026-07-20T20:00:00"
    assert session.ledger.save.entities["朱明"].states[0].text == "左腿摔伤"


# ---------------------------------------------------------------------------
# 多次大总结 = 滚动
# ---------------------------------------------------------------------------


def test_second_digest_rolls_the_previous_text_up(session) -> None:
    _drain(session)
    apply_digest(session.ledger, text="第一版编年史")

    previous, events = digest_source(session.ledger)
    assert previous == "第一版编年史"
    assert events == [], "旧总结不在原料里——否则会被当成一件发生过的事重讲"

    _turn(session, at="2026-07-15T09:00:00", location="主街", participants=["刘星"], body="新的一天。")
    previous, events = digest_source(session.ledger)
    assert previous == "第一版编年史"
    assert [ev["body"] for ev in events] == ["新的一天。"]

    result = apply_digest(session.ledger, text="第二版编年史")
    assert result["revision"] == 2
    assert len(session.ledger.narratives) == 1


def test_digest_overview_reports_the_state(session) -> None:
    before = digest_overview(session.ledger)
    assert before["previous"] is None
    assert before["pending_count"] == 1  # 开场白

    apply_digest(session.ledger, text="编年史", summary="一段往事")

    after = digest_overview(session.ledger)
    assert after["previous"] == {
        "revision": 1,
        "at": after["clock"],
        "summary": "一段往事",
        "text": "编年史",
    }
    assert after["pending_count"] == 0


def test_pending_events_excludes_only_the_digest(session) -> None:
    _drain(session)
    apply_digest(session.ledger, text="编年史")
    _turn(session, at="2026-07-15T09:00:00", location="主街", participants=["刘星"], body="新事")
    assert [ev["body"] for ev in pending_events(session.ledger)] == ["新事"]


# ---------------------------------------------------------------------------
# 起草 worker
# ---------------------------------------------------------------------------


class _StubLLM:
    """按调用次序回话的桩（大总结会分多次调）。"""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.messages: list[list[dict]] = []

    async def complete_json(self, messages, schema, **kwargs):
        self.messages.append(messages)
        return self.replies.pop(0)


def _await(coro):
    """仓库没有异步测试插件（pytest-asyncio 未装），异步 worker 用 asyncio.run 驱动。"""
    import asyncio

    return asyncio.run(coro)


def test_run_digest_single_pass() -> None:
    llm = _StubLLM([{"summary": "一句话", "text": "编年史正文"}])
    out = _await(run_digest(llm, "", [{"at": "2026-07-14T09:00:00", "location": "主街", "body": "事"}]))
    assert out == {"summary": "一句话", "text": "编年史正文", "chunks": 1, "event_count": 1}
    assert "还没有编年史" in llm.messages[0][1]["content"]


def test_run_digest_forwards_the_previous_chronicle() -> None:
    llm = _StubLLM([{"summary": "", "text": "续写"}])
    _await(run_digest(llm, "很久以前发生过这些事", [{"at": "x", "location": "主街", "body": "事"}]))
    assert "很久以前发生过这些事" in llm.messages[0][1]["content"]


def test_final_prompt_forces_a_self_contained_merge() -> None:
    """🔴 回归守卫（2026-10-12 实机事故）。

    原提示词写「在它基础上续写：旧事不再展开，把篇幅让给后来的事」，user 头里又写
    「旧事不要展开重讲」。模型据此把旧编年史**整段丢掉**——交回来的新编年史以
    "（承前）"开头、旧内容一条没有，而旧那条随即被替换掉 ⇒ 信息真丢了。

    ⚠️ 原来的 ``test_run_digest_forwards_the_previous_chronicle`` **抓不到**这个 bug：
    它只验"旧正文在提示词里"，而那正是当时成立的——**传进去 ≠ 要求并进来**。

    本用例钉两件事：① 必须明写"合并重写 / 不许整段消失 / 自包含"；
    ② 不许再出现"别写旧的"这类反向措辞（它们正是事故成因）。
    """
    from app.workers.digest import _final_messages

    messages = _final_messages("很久以前发生过这些事", "（新事）")
    whole = messages[0]["content"] + "\n" + messages[1]["content"]

    # ① 旧正文必须进提示词——不然模型根本无从合并。
    assert "很久以前发生过这些事" in messages[1]["content"]

    # ② 必须明写硬要求。
    for required in ("合并重写", "不许整段消失", "自包含"):
        assert required in whole, f"提示词缺少硬要求：{required}"

    # ③ 反向措辞一个都不许有。
    for forbidden in ("旧事不再展开", "旧事不要展开重讲", "把篇幅让给后来的事"):
        assert forbidden not in whole, f"提示词又出现了叫模型省略旧事的话：{forbidden}"


def test_run_digest_chunks_long_material() -> None:
    """超长原料先分段压要点、再合成——否则一次塞不下。"""
    llm = _StubLLM(
        [
            {"text": "第一段要点"},
            {"text": "第二段要点"},
            {"text": "第三段要点"},
            {"text": "第四段要点"},
            {"summary": "汇总", "text": "编年史"},
        ]
    )
    events = [{"at": str(i), "location": "主街", "body": "x" * 5000} for i in range(4)]
    out = _await(run_digest(llm, "", events))
    assert out["chunks"] == 4
    assert out["text"] == "编年史"
    assert len(llm.messages) == out["chunks"] + 1
    assert "第一段要点" in llm.messages[-1][1]["content"]


# ---------------------------------------------------------------------------
# API：先草稿、后落地；闸门在压缩之前
# ---------------------------------------------------------------------------


def _client(tmp_path, llm=None) -> TestClient:
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, data_dir=tmp_path / "data", candidate_ttl_days=7)
    return TestClient(create_app(settings=settings, llm=llm or FakeLLM({"*": GENERIC_LLM_RESPONSE})))


def test_digest_preview_returns_a_draft_without_writing(tmp_path) -> None:
    llm = FakeLLM({"*": {"summary": "一句话", "text": "编年史正文"}})
    with _client(tmp_path, llm=llm) as client:
        sid = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}).json()["sid"]

        r = client.post(f"/api/sessions/{sid}/digest/preview")
        assert r.status_code == 200
        assert r.json()["text"] == "编年史正文"

        # 草稿不落盘：事件流一条没少。
        status = client.get(f"/api/sessions/{sid}/digest").json()
        assert status["previous"] is None
        assert status["pending_count"] == 1


def test_digest_commit_writes_and_reports(tmp_path) -> None:
    with _client(tmp_path) as client:
        sid = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}).json()["sid"]

        r = client.post(f"/api/sessions/{sid}/digest", json={"text": "编年史", "summary": "往事"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["revision"] == 1

        status = client.get(f"/api/sessions/{sid}/digest").json()
        assert status["previous"]["text"] == "编年史"


def test_digest_commit_blocks_on_pending_candidates(tmp_path) -> None:
    """候选是针对旧事件写的正文——旧事件一压，玩家回去采纳时上下文就没了。"""
    with _client(tmp_path) as client:
        sid = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}).json()["sid"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "去网吧找朱明"})
        assert client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"]

        r = client.post(f"/api/sessions/{sid}/digest", json={"text": "编年史"})
        assert r.status_code == 409
        assert "候选" in r.json()["detail"]


def test_digest_commit_needs_something_to_digest(tmp_path) -> None:
    with _client(tmp_path) as client:
        sid = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}).json()["sid"]
        assert client.post(f"/api/sessions/{sid}/digest", json={"text": "编年史"}).status_code == 200
        # 压完就没有新事件了 —— 再压一次是空转。
        r = client.post(f"/api/sessions/{sid}/digest", json={"text": "又一遍"})
        assert r.status_code == 400
        assert client.post(f"/api/sessions/{sid}/digest/preview").status_code == 400
