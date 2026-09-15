"""角色状态 · 长期事实（Step 2a）端到端冒烟：真实 LLM 一回合 → 审计提议 → 采纳落地 → 磁盘回读。

用法：
    python scripts/smoke_state_real.py

做四件事（全程在 qingshi2 的**临时副本**上跑，绝不碰真实存档）：
 1. 种两条状态：主角一条 ``until`` 已过期（验证规则侧到期清算）、朱明一条常驻；
 2. 离线打印审计工单里的「角色状态」段（验证注入 + 带 id）；
 3. 跑一条真实回合（输入写死"受伤"情节，逼审计必须报 state_add），打印审计原始
    state_add / state_remove；
 4. 采纳后从**磁盘**回读 save.json 与 events.jsonl，核对 entries / last_state_change /
    source=="state" 的记账事件。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.llm import LLMGateway
from app.core.presets import load_global_preset
from app.core.workorder import build_audit_work_order
from app.ledger.save import EntityRuntime, StateItem
from app.runtime.session import open_session
from app.runtime.turn import TurnRunner

SRC = Path(__file__).resolve().parent.parent / "content" / "qingshi2"
INPUT = (
    "刘星骑车赶去鱼市，路上被一辆三轮车撞倒，左小腿被车斗划开一道口子，"
    "血流了一路，他忍着疼一瘸一拐地走到了鱼市。"
)
# 命令行可覆盖输入（用来试"什么该上状态、什么不该"）：
#   python scripts/smoke_state_real.py "刘星听说王蓉她妈住院了，又在鱼市被人砍伤了左手"
if len(sys.argv) > 1:
    INPUT = sys.argv[1]

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))


async def main() -> None:
    settings = get_settings()
    preset = load_global_preset(settings.data_dir / "presets.json")

    with tempfile.TemporaryDirectory() as td:
        world = Path(td) / "qingshi2"
        shutil.copytree(SRC, world)
        session = open_session(world)
        ledger = session.ledger
        player = ledger.player_name()
        print(f"世界副本：{world}\n主角：{player}\n初始时钟：{ledger.save.clock}\n")

        # ── 1. 种状态 ────────────────────────────────────────────────
        ledger.save.entities[player] = EntityRuntime(
            states=[
                StateItem(
                    text="左臂旧伤未愈",
                    since="2001-07-09T08:00:00",
                    until="2001-07-09T20:00:00",  # 已过期 → 本回合应被清算
                ),
                StateItem(
                    text="随身揣着王蓉还的半张电影票",
                    since="2001-07-09T20:00:00",
                    public=False,
                ),
            ]
        )
        ledger.save.entities["朱明"] = EntityRuntime(
            states=[
                StateItem(
                    text="额角缠着纱布",
                    since="2001-07-09T18:00:00",
                    public=True,
                )
            ]
        )
        ledger.persist_save()

        # ── 2. 审计工单离线预览 ──────────────────────────────────────
        order = build_audit_work_order(
            session.world, ledger, ledger.current_scene()
        )
        block = [
            ln
            for ln in order.splitlines()
            if "st_" in ln or "角色状态（长期事实" in ln or "另有" in ln
        ]
        print("审计工单·角色状态段：")
        for ln in block:
            print("   ", ln)
        check(
            "审计工单带状态 id（移除要靠它精确定位）",
            any("[st_" in ln for ln in block),
        )
        check(
            "主角两条都已注入（上限 6 之内）",
            sum(ln.count("[st_") for ln in block) >= 2,
        )

        # ── 3. 真实回合 + 采纳 ───────────────────────────────────────
        llm = LLMGateway(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            max_concurrency=2,
        )
        runner = TurnRunner(session, llm, settings, preset=preset)

        print("\n跑一条真实回合（受伤情节）…")
        candidate = await runner.run_turn(INPUT)
        print("\n正文：\n" + candidate.prose + "\n")

        captured: dict = {}

        async def audit_cb(cand):
            out = await runner._audit_callback(cand)
            captured["out"] = out
            return out

        await runner.transaction.commit(candidate.candidate_id, audit=audit_cb)
        audit_out = captured.get("out")
        print("审计 state_add：", json.dumps(audit_out.state_add, ensure_ascii=False))
        print("审计 state_remove：", json.dumps(audit_out.state_remove, ensure_ascii=False))

        # ── 4. 磁盘回读 ──────────────────────────────────────────────
        print("\n---- 磁盘复核 ----")
        save = json.loads((world / "save.json").read_text(encoding="utf-8"))
        ents = save.get("entities", {})
        change = save.get("last_state_change") or {}
        print("last_state_change =", json.dumps(change, ensure_ascii=False, indent=2))
        print("entities =", json.dumps(ents, ensure_ascii=False, indent=2))

        events = [
            json.loads(ln)
            for ln in (world / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        state_events = [e for e in events if e.get("source") == "state"]
        print("\nsource=='state' 记账事件：")
        for e in state_events:
            print(
                f"    {e['id']}  at={e.get('at')}  loc={e.get('location')!r}"
                f"  participants={e.get('participants')}  body={e.get('body')!r}"
                f"  summary={e.get('summary')}"
            )

        add_made = change.get("added") or []
        exp_made = change.get("expired") or []
        rm_made = change.get("removed") or []

        # 断言对任意输入都成立（自定义输入可能**故意不含**长期变化 → added 空是对的）：
        # 有新增就必须可追溯；磁盘与内存必须一致。
        if add_made:
            check("审计提议落地（正文写伤 → 至少 1 条 state_add）", True, f"added={add_made}")
        else:
            print(f"[--] 本次审计没有提议新状态（added 空）——自定义输入下这可能正是期望结果")
        check(
            "到期清算跑通（until 过期 → 1 条 expired）",
            len(exp_made) == 1,
            f"expired={exp_made}",
        )
        check(
            "新增条目若存在，必带 source_event 且能指回事件流",
            all(any(e["id"] == a["event_id"] for e in state_events) for a in add_made),
        )
        check(
            "记账事件形状正确（body 空 / location None / known_by 含主角）",
            bool(state_events)
            and all(
                e["body"] == ""
                and e["location"] is None
                and len(e["participants"]) == 1  # 主体只有本人
                and player in e["known_by"]
                for e in state_events
            ),
        )
        # Step 2c（2026-09-14 晚）改了语义：到期**失效不删条目**——条目留在存档里
        # 供玩家查看与撤销，只有注入侧过滤它（旧断言"已从列表移除"是当时的口径）。
        check(
            "过期条目留在存档里但已标记失效（Step 2c：失效不删除）",
            all(
                any(
                    s["text"] == x["text"] and s.get("expired_at")
                    for s in ents.get(x["npc_id"], {}).get("states", [])
                )
                for x in exp_made
            ),
        )
        mem = {
            n: len(rt.states) for n, rt in ledger.save.entities.items() if rt.states
        }
        disk = {
            n: len(v.get("states") or []) for n, v in ents.items() if v.get("states")
        }
        check("磁盘 entities 与内存一致（回读校验）", mem == disk, f"内存={mem} 磁盘={disk}")

    print("\n================ 汇总 ================")
    failed = [n for n, ok, _ in RESULTS if not ok]
    for name, ok, detail in RESULTS:
        print(f"  {'✔' if ok else '✘'} {name}")
    print(f"\nS{'TEP 2a SMOKE FAILED: ' + ', '.join(failed) if failed else 'TEP 2a SMOKE PASSED'}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"STEP 2a SMOKE FAILED: {type(exc).__name__}: {exc}")
        raise
