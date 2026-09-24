"""候选手改：就地改正文 + 摘要对齐（2026-09-24）。

玩家原话：「编剧写出来的东西一半满意一半不满意，采纳只能整体采纳」。重抽是从头
再写（见 ``reroll``，不承接上一稿），所以"局部不满意"只剩手改一条路。

两条纪律，每条都有用例钉住：

1. **就地改**：改的是原候选本身，不新开版本——多一个版本就要回答"我在改哪一份"，
   而玩家的意图只有一个：把这一稿变成我想要的样子。
2. **摘要跟着正文走**：摘要此后每轮都被压成一行重新装配给编剧与导演，陈旧的它
   要么误导后续回合，要么把玩家刚删掉的私密信息继续传下去（脱敏侧门）。
   判定交给辅助模型（``sync_summary``），判据与质检里那条 summary 字段同一套。

对应代码：``app/runtime/turn.py::TurnRunner.edit_candidate`` · ``app/workers/qc.py::sync_summary``
· ``app/api/routes_turn.py::edit_candidate``。
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from app.runtime.turn import TurnRunner
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT

EDITED = "朱明从网吧出来，挑眉看了你一眼，把烟头踩灭。"


class _SummaryLLM(FakeLLM):
    """手改那一笔（``SummaryOutput``）单独给响应，其余工位照旧走 ``responses``。

    **为什么按 schema 分派**：摘要核对的 user 消息以**正文**开头，而正文每次都不
    一样，按前缀匹配（``PrefixKeyLLM`` 那套）必然串到编剧/质检的桩上去。schema 名
    才是"这一笔是谁"的稳定标识——与 ``test_turn._AuditBoomLLM`` 同一条路子。

    ``summary=None`` = 假装核对员什么都没说（沿用旧摘要）；``""`` 同义。
    手改那笔**不进** ``calls``（那是回合管线的账），单独记在 ``summary_calls``，
    于是 ``len(llm.calls)`` 这类断言照旧只看回合。
    """

    def __init__(self, responses, *, summary: str | None = None):
        super().__init__(responses)
        self.summary = summary
        self.summary_calls: list[dict] = []

    async def complete_json(self, messages, schema, *, model="fake", temperature=0.2, worker=""):
        if getattr(schema, "__name__", "") == "SummaryOutput":
            self.summary_calls.append({"messages": messages, "model": model, "worker": worker})
            self.usage["calls"] += 1
            return {"summary": self.summary or ""}
        return await super().complete_json(
            messages, schema, model=model, temperature=temperature, worker=worker
        )


def _runner(session, llm, settings) -> TurnRunner:
    return TurnRunner(session, llm, settings)


def _draft(session, llm, settings):
    """跑一轮，拿到一份待采纳的候选。"""
    return asyncio.run(_runner(session, llm, settings).run_turn("问朱明昨天的事"))


def _prompt_of(call: dict) -> str:
    return "\n".join(m.get("content") or "" for m in call["messages"])


# ---------------------------------------------------------------------------
# 就地改 + 摘要对齐
# ---------------------------------------------------------------------------


def test_edit_replaces_the_prose_in_place_and_syncs_the_summary(session, settings):
    """手改 = 改原候选（不新开一份），摘要取核对员的修正版。"""
    llm = _SummaryLLM({"*": GENERIC_LLM_RESPONSE}, summary="朱明出门碰见刘星，踩灭烟头搭话。")
    runner = _runner(session, llm, settings)
    candidate = _draft(session, llm, settings)
    old_summary = candidate.side_effects.narrative["summary"]

    edited = asyncio.run(runner.edit_candidate(candidate.candidate_id, prose=f"  {EDITED}  "))

    # ① 正文换掉了（首尾空白顺手吃掉——它是编辑框带进来的，不该进事件日志）
    assert edited.prose == EDITED
    # ② 摘要跟着换，且真的取自核对员
    assert edited.side_effects.narrative["summary"] == "朱明出门碰见刘星，踩灭烟头搭话。"
    assert edited.side_effects.narrative["summary"] != old_summary
    # ③ **就地**：还是同一份候选、同一个回合，没有多出第二个版本
    assert edited.candidate_id == candidate.candidate_id
    assert edited.turn_id == candidate.turn_id
    assert [c.candidate_id for c in session.candidates.list_for_turn(candidate.turn_id)] == [
        candidate.candidate_id
    ]
    # ④ 落盘读回一致（刷新页面看到的是改后的，不是内存里那份）
    stored = session.candidates.load(candidate.candidate_id)
    assert stored.prose == EDITED
    assert stored.side_effects.narrative["summary"] == "朱明出门碰见刘星，踩灭烟头搭话。"
    assert len(llm.summary_calls) == 1


def test_edit_keeps_the_old_summary_when_the_checker_stays_silent(session, settings):
    """改的只是措辞/氛围 → 核对员不给 summary → **沿用旧摘要**，不能清空也不能瞎编。"""
    llm = _SummaryLLM({"*": GENERIC_LLM_RESPONSE}, summary="")
    runner = _runner(session, llm, settings)
    candidate = _draft(session, llm, settings)
    old_summary = candidate.side_effects.narrative["summary"]

    edited = asyncio.run(runner.edit_candidate(candidate.candidate_id, prose=EDITED))

    assert edited.prose == EDITED
    assert edited.side_effects.narrative["summary"] == old_summary == GENERIC_LLM_RESPONSE["summary"]
    assert len(llm.summary_calls) == 1  # 核对还是跑了（问过才知道不用改）


def test_edit_without_a_real_change_does_not_call_the_model(session, settings):
    """正文没变（点了保存又后悔、或只删了个空格）→ 连那笔核对都不发。

    这是最常见的一种"保存"，为它敲一次模型纯属浪费；也顺带保证"打开编辑框再关掉"
    不会在事件日志/用量里留下任何痕迹。
    """
    llm = _SummaryLLM({"*": GENERIC_LLM_RESPONSE}, summary="不该被用到的摘要")
    runner = _runner(session, llm, settings)
    candidate = _draft(session, llm, settings)
    before = candidate.side_effects.narrative["summary"]

    same = asyncio.run(runner.edit_candidate(candidate.candidate_id, prose=candidate.prose))

    assert same.prose == candidate.prose
    assert same.side_effects.narrative["summary"] == before
    assert llm.summary_calls == []


def test_edit_reference_uses_this_candidates_participants(session, settings):
    """摘要核对的参照区 = **这一稿自己存下的比对角**（与重抽同一条纪律：在场 ≠ 上缴过）。

    没这一步，摘要只按"当前场景 + 主角边界"核对，NPC 的知识边界一条都查不到——
    而摘要正是那个会被反复装配、把泄漏反复带出去的字段。
    """
    llm = _SummaryLLM({"*": GENERIC_LLM_RESPONSE}, summary="新摘要")
    runner = _runner(session, llm, settings)
    candidate = _draft(session, llm, settings)
    # 夹具的假响应没有 actor_questions，自然产不出 NPC → 按落盘格式补一次基准。
    candidate.participants = ["刘星", "朱明"]
    session.candidates.save(candidate)

    asyncio.run(runner.edit_candidate(candidate.candidate_id, prose=EDITED))

    call = llm.summary_calls[0]
    prompt = _prompt_of(call)
    assert "朱明 知道的事：" in prompt  # 参照区带上了这一稿的角色
    assert EDITED in prompt  # 比对的正文是**手改后**的那份
    assert GENERIC_LLM_RESPONSE["summary"] in prompt  # 旧摘要也交给它比对
    assert call["worker"] == "qc"  # 走**辅助**出口（"谁是辅助"只有 AUX_WORKERS 一份名单）


# ---------------------------------------------------------------------------
# 守卫
# ---------------------------------------------------------------------------


def test_edit_rejects_blank_and_unknown_before_any_model_call(session, settings):
    """空正文 400、找不到 404，且两次都在**发模型之前**就被挡下。"""
    llm = _SummaryLLM({"*": GENERIC_LLM_RESPONSE}, summary="新摘要")
    runner = _runner(session, llm, settings)
    candidate = _draft(session, llm, settings)

    with pytest.raises(ValueError):
        asyncio.run(runner.edit_candidate(candidate.candidate_id, prose="   \n  "))
    with pytest.raises(KeyError):
        asyncio.run(runner.edit_candidate("cand_不存在", prose=EDITED))

    assert llm.summary_calls == []
    assert candidate.prose == GENERIC_LLM_RESPONSE["prose"]  # 原稿没被动过


def test_edit_is_gone_once_the_candidate_is_adopted(session, settings):
    """已采纳的稿子改不动：采纳会把候选文件删掉，于是 load 取不到 → 404。

    **这条正是"不查 status"的实证**：全仓没有一处会把 status 改成 pending 以外的
    值，所以"已采纳"在磁盘上的表现就是"文件没了"。查 status 是一条没有牙齿的分支。
    """
    llm = _SummaryLLM({"*": GENERIC_LLM_RESPONSE}, summary="新摘要")
    runner = _runner(session, llm, settings)
    candidate = _draft(session, llm, settings)
    asyncio.run(runner.adopt(candidate.candidate_id))

    assert session.candidates.load(candidate.candidate_id) is None
    with pytest.raises(KeyError):
        asyncio.run(runner.edit_candidate(candidate.candidate_id, prose=EDITED))
    assert llm.summary_calls == []


# ---------------------------------------------------------------------------
# 端点接线
# ---------------------------------------------------------------------------


def _client(tmp_path) -> TestClient:
    """与 ``test_api._make_client`` 同一套（world_root 与 data_dir 都要沙箱化）。"""
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(
        content_root=tmp_path,
        data_dir=tmp_path / "data",
        candidate_ttl_days=7,
    )
    return TestClient(create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE})))


def test_put_candidate_endpoint(tmp_path):
    """PUT 接线：200 + 改后的候选 / 无正文 400 / 找不到 404。

    这里不重复验摘要逻辑（上面已经钉过），只验**路由层**：方法、状态码、返回体是
    候选的完整 dump（前端拿它原地替换 state.candidates 里那一项）。
    """
    with _client(tmp_path) as client:
        sid = client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}).json()["sid"]
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明昨天的事"})
        cid = client.get(f"/api/sessions/{sid}/candidates/pending").json()["candidates"][0]["candidate_id"]

        r = client.put(f"/api/sessions/{sid}/candidates/{cid}", json={"prose": EDITED})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["candidate_id"] == cid and body["prose"] == EDITED
        # 前后端契约：前端读 side_effects 之外只读 id/prose，这里顺带钉住它在
        assert body["side_effects"]["narrative"]["summary"]

        r = client.put(f"/api/sessions/{sid}/candidates/{cid}", json={"prose": "   "})
        assert r.status_code == 400
        r = client.put(f"/api/sessions/{sid}/candidates/cand_不存在", json={"prose": EDITED})
        assert r.status_code == 404
