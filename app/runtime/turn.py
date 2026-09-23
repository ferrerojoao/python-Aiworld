from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import Any, NamedTuple

from app.config import Settings
from app.core.store import new_id
from app.rules.lorebook import hit_entry_ids, merge_active_lore
from app.rules.movement import resolve_destination
from app.rules.route import looks_like_move
from app.runtime.session import GameSession
from app.runtime.transaction import Candidate, SideEffects, Transaction
from app.workers.actor import run_actor
from app.workers.auditor import run_audit
from app.workers.qc import build_qc_reference, run_qc
from app.workers.schemas import ActorQuestion, WriterOutput
from app.workers.writer import run_writer
from app.world.models import NarrativePreset


class NeedChooseCandidate(Exception):
    """Raised when a new turn is submitted while multiple candidates are pending."""


_OOC_RE = re.compile(r"\(\((.+?)\)\)", re.S)


def _extract_directive(text: str) -> tuple[str, str]:
    """Peel ``((...))`` director directives out of the raw input.

    Directives are per-turn writing requirements for the writer (style,
    emphasis). They never enter the story: the remaining text is what gets
    routed and recorded as the player input; directives ride a separate
    writer message and are stored on the candidate so rerolls keep them.
    """
    parts = [m.strip() for m in _OOC_RE.findall(text) if m.strip()]
    clean = _OOC_RE.sub(" ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    directive = "\n".join(f"- {p}" for p in parts)
    return clean, directive


def _resolve_npc(
    raw: str,
    question: str,
    context: str,
    present: list[str],
    npcs: Mapping[str, Any],
) -> str | None:
    """编剧上缴的 ``npc_id`` 纠偏。

    模型偶尔填「你」「玩家」或自造 id——这类值能通过 pydantic 校验却永远匹配
    不上，静默丢弃最难查。按确定性递进救回（与 ``transaction._resolve_scene``
    同一思路）：精确命中 → 在场名单里唯一一个名字出现在上缴串里的 → 唯一一个
    名字出现在问句/处境里的 → 放弃（返回 None，由调用方丢弃并给 warning，
    绝不静默）。
    """
    raw = (raw or "").strip()
    if raw and raw in npcs:
        return raw
    names = [pid for pid in present if pid and pid in npcs]
    if raw:
        hits = [pid for pid in names if pid in raw]
        if len(hits) == 1:
            return hits[0]
    blob = f"{question}\n{context}"
    hits = [pid for pid in names if pid in blob]
    if len(hits) == 1:
        return hits[0]
    return None


class WriteResult(NamedTuple):
    """``_write_turn`` 的产出：成稿 + 落点备注 + 两稿上缴过的角色（QC 比对基准）。"""

    output: WriterOutput
    notes: list[dict]
    participants: list[str]


class TurnRunner:
    def __init__(
        self,
        session: GameSession,
        llm,
        settings: Settings,
        preset: NarrativePreset | None = None,
    ):
        self.session = session
        self.llm = llm
        self.settings = settings
        self.preset = preset or session.world.presets
        self.transaction = Transaction(session.ledger, session.candidates)
        self._progress_queue = None

    def set_progress_queue(self, queue) -> None:
        self._progress_queue = queue

    async def _progress(self, stage: str, label: str) -> None:
        if self._progress_queue is not None:
            await self._progress_queue.put({"type": "stage", "stage": stage, "label": label})

    def _actor_ticket(self, npc_id: str, scene_id: str) -> bool:
        """可派 = 配了 Actor **且此刻在场**。

        不在场的一律不派：Actor 工作单按当前场景拼装，缺席者会拿到错位的处境
        （2026-09-11 修 2×2 越界派发的 B 格）。在场名单与编剧工作单里的「在场 NPC」
        同源（都走 ``present_at``），所以编剧的正常上缴不会被这条门误拦。
        """
        npc = self.session.world.npcs.get(npc_id)
        if npc is None or not npc.has_actor:
            return False
        return npc_id in self.session.ledger.present_at(scene_id)

    async def _write_turn(
        self,
        player_input: str,
        *,
        scene: str,
        rule_bundle: dict | None = None,
        rewrite_note: str = "",
        writer_directive: str = "",
    ) -> WriteResult:
        """Writer pass, with the deep-choice two-stage loop.

        编剧上缴 = 它在那一拍**停笔**（2026-09-11 用户实测：只写吃早饭和发问的
        引子，朱明的反应根本没写）。所以**只要有上缴就一定有第二稿**——一稿在
        定义上就不完整，照用必然缺戏。能不能派 Actor 只决定第二稿的输入：

        - 派得出去（在场 ∩ 配 Actor，且 ``npc_id`` 能识别）→ 先问 Actor，
          第二稿按本人决定重写那一拍；
        - 派不出去（无票 / 不在场 / ``npc_id`` 无法识别）→ 第二稿明说「由你直接
          拍板、不要把这一拍留空」。

        第二稿以一稿为底稿（assistant 消息回填，见 ``run_writer``），输出仍是
        整场全文——不再出现"第一稿被丢弃、第二稿只写后半段"。
        """
        await self._progress("writer", "编排成文中")
        first = await run_writer(
            self.llm,
            self.session.world,
            self.session.ledger,
            player_input,
            rule_bundle=rule_bundle,
            scene_id=scene,
            preset=self.preset,
            rewrite_note=rewrite_note,
            writer_directive=writer_directive,
            limits=self.settings.limits(),
            # 模型名与**出口**（主 / 辅两套 base_url）都由角色决定，不再在这里
            # 各自取一次：run_writer 的默认 worker="story" → LLMRouter 一处判定。
            temperature=self.settings.temp_writer,
        )
        out = first
        notes: list[dict] = []
        participants = {q.npc_id for q in first.actor_questions}

        if first.actor_questions:
            present = self.session.ledger.present_at(scene)
            dispatch: list[tuple[str, ActorQuestion]] = []
            own: list[tuple[str, str]] = []
            for q in first.actor_questions:
                npc_id = _resolve_npc(
                    q.npc_id, q.question, q.context, present, self.session.world.npcs
                )
                if npc_id is None:
                    notes.append(
                        {
                            "level": "warning",
                            "desc": f"编剧上缴的「{q.npc_id or '未署名'}」无法识别为角色，"
                            "本轮未派 Actor，该抉择由编剧自行写入正文",
                        }
                    )
                    own.append((q.npc_id or "未署名角色", q.question))
                    continue
                npc = self.session.world.npcs[npc_id]
                if npc_id == self.session.ledger.player_name():
                    # 编剧把主角自己上缴了：主角的抉择只能由玩家给（本就写进正文的
                    # 玩家输入），引擎恒不派 Actor。留痕不静默。
                    notes.append(
                        {
                            "level": "info",
                            "desc": f"编剧上缴了主角「{npc_id}」的抉择——主角不派 Actor，"
                            "该拍由编剧依玩家输入落笔",
                        }
                    )
                    own.append((npc_id, q.question))
                    continue
                if npc.has_actor and npc_id in present:
                    dispatch.append((npc_id, q))
                    continue
                if not npc.has_actor:
                    notes.append(
                        {
                            "level": "info",
                            "desc": f"{npc_id} 未配 Actor，本轮其深抉择由编剧直接拍板"
                            "（如需隔离决策，可在世界工作台勾选「使用 Actor」）",
                        }
                    )
                else:
                    notes.append(
                        {
                            "level": "warning",
                            "desc": f"{npc_id} 不在当前场景，本轮未派 Actor，"
                            "其深抉择由编剧直接写入正文",
                        }
                    )
                own.append((npc_id, q.question))

            decisions: list[str] = []
            if dispatch:
                await self._progress("actor", "NPC Agent 思考中")
                for npc_id, q in dispatch:
                    npc = self.session.world.npcs[npc_id]
                    decision = await run_actor(
                        self.llm,
                        self.session.world,
                        self.session.ledger,
                        npc,
                        q.question,
                        context=q.context,
                        scene_id=scene,
                        limits=self.settings.limits(),
                        temperature=0.8,
                    )
                    decisions.append(
                        f"- {npc.id}：{decision.decision}；行为：{decision.action_hint}；语气：{decision.tone}"
                    )

            await self._progress("writer", "按NPC决策改稿中")
            out = await run_writer(
                self.llm,
                self.session.world,
                self.session.ledger,
                player_input,
                rule_bundle=rule_bundle,
                scene_id=scene,
                preset=self.preset,
                prior_output=first,
                actor_decisions="\n".join(decisions),
                own_decisions="\n".join(f"- {name}：{question}" for name, question in own),
                rewrite_note=rewrite_note,
                writer_directive=writer_directive,
                limits=self.settings.limits(),
                temperature=self.settings.temp_writer,
            )
            participants |= {q.npc_id for q in out.actor_questions}

        # 改稿后仍冒出的上缴：一轮只跑一次两段式，不再追派。
        notes.extend(self._repeat_notes(out))
        return WriteResult(output=out, notes=notes, participants=sorted(participants))

    def _repeat_notes(self, out) -> list[dict]:
        """改稿后仍上缴的问题：一轮只跑一次两段式，不再追派（minor，留痕不静默）。"""
        return [
            {
                "level": "minor",
                "desc": f"改稿后仍提出{q.npc_id or '未署名角色'}的未决深抉择"
                f"（{q.question[:40]}），本回合不再追派",
            }
            for q in out.actor_questions
        ]

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    async def run_turn(self, player_input: str) -> Candidate:
        pending = self.session.candidates.list_pending()
        if pending:
            if len(pending) == 1:
                await self.transaction.commit(pending[0].candidate_id, audit=self._audit_callback)
            else:
                raise NeedChooseCandidate(
                    "当前回合有多份候选，请先选择采纳哪一份或放弃当前回合。"
                )

        # 本回合导演指令：((...)) 行内语法，剥离后不进路由/不进账本。
        player_input, writer_directive = _extract_directive(player_input)

        # 规则段只做一件事：**移动**。原先的"跳时"解析已于 2026-09-16 整条下线
        # （原委见 ``app/rules/route.py`` 模块注释）——时间全交审计，
        # 规则侧不再产出 settle_to / delta，也就没有跨工位重复计费的问题。
        scene = self.session.ledger.current_scene()
        rule_bundle: dict = {"scene": scene}

        if looks_like_move(player_input):
            dest = resolve_destination(player_input, self.session.world, self.session.ledger)
            if dest:
                rule_bundle.update({"destination": dest, "scene": dest})
                scene = dest

        # World book pre-solve: this turn's player input triggers concepts
        # (merge into the active list previous prose facts still hold slots).
        hits = hit_entry_ids(self.session.world, player_input)
        if hits:
            self.session.ledger.save.active_lore_ids = merge_active_lore(
                self.session.ledger.save.active_lore_ids, hits
            )

        result = await self._write_turn(
            player_input,
            scene=scene,
            rule_bundle=rule_bundle,
            writer_directive=writer_directive,
        )
        out = result.output
        player = self.session.ledger.player_name()

        # 两稿上缴过的角色都进比对基准：二稿不再提，不代表这一拍没发生过
        # （诊断成因 #6：participants 侧漏会让相关 NPC 从质检里掉出去）。
        # **放在 if 之外算**：它要随候选落盘（见下面 Candidate），所以关掉质检的那一轮
        # 也得有值——否则后面重抽取不到"这一拍上缴过的角色"，比对基准会退化成空表。
        qc_participants = [player] + [pid for pid in result.participants if pid != player]

        if self.settings.qc_enabled:
            await self._progress("qc", "质检员审校中")
            qc = await run_qc(
                self.llm,
                self.session.world,
                self.session.ledger,
                out.prose,
                preset=self.preset,
                reference=build_qc_reference(
                    self.session.world,
                    self.session.ledger,
                    qc_participants,
                    known_limit=self.settings.limits().known_set_limit,
                ),
                summary_hint=out.summary,
                temperature=self.settings.temp_qc,
            )
            prose = qc.prose
            issues = qc.issues
            # 摘要随正文一同过质检：正文被脱敏/改写而摘要未同步时，以质检修正版为准
            # （防泄漏经事件日志摘要侧门回归——summary 会长期反复装配）。
            summary_hint = qc.summary or out.summary
        else:
            # 跳过质检：编剧初稿直接进候选（设置项，玩家自担文风/泄漏风险）。
            prose = out.prose
            issues = []
            summary_hint = out.summary

        # Build one candidate version.
        summary = summary_hint or (prose or "")[:40]
        turn_id = new_id("turn")
        candidate_id = new_id("cand")
        now = dt.datetime.now().isoformat(timespec="seconds")
        candidate = Candidate(
            candidate_id=candidate_id,
            turn_id=turn_id,
            trace_id=new_id("tr"),
            mode="initial",
            player_input=player_input,
            writer_directive=writer_directive,
            prose=prose,
            side_effects=SideEffects(
                # 规则段只可定移动目的地；正文语义副作用（时间/在场/私密）由采纳时审计推断。
                narrative={
                    "location": rule_bundle.get("destination"),
                    "summary": summary,
                },
                events=[],
            ),
            conflicts=issues + result.notes,
            # 质检比对基准随候选落盘：重抽那一笔要用它（2026-09-23 用户定：补存，不做兜底）。
            participants=qc_participants,
            created_at=now,
            updated_at=now,
        )
        self.session.candidates.save(candidate)
        return candidate

    async def reroll(self, turn_id: str, mode: str = "rephrase", note: str = "") -> Candidate:
        turn_candidates = self.session.candidates.list_for_turn(turn_id)
        if not turn_candidates:
            raise KeyError(f"no candidates for turn: {turn_id}")
        latest = turn_candidates[-1]

        scene = (
            latest.side_effects.narrative.get("location")
            if latest.side_effects.narrative
            else self.session.ledger.current_scene()
        )
        rewrite_note = ""
        if mode in {"rephrase", "retarget"}:
            parts = ["围绕上一稿，保持同一剧情走向重写正文"]
            if note:
                parts.append(f"（玩家要求：{note}）")
            rewrite_note = "；".join(parts)

        result = await self._write_turn(
            latest.player_input,
            scene=scene,
            rewrite_note=rewrite_note,
            writer_directive=latest.writer_directive,
        )
        out = result.output
        player = self.session.ledger.player_name()
        # 重抽同样要过质检，比对基准 = **这一拍上缴过的角色** = 新稿上缴的 ∪ 上一稿存下的。
        # 以前这里给 build_qc_reference 传的是空表 ⇒ 重抽稿只查"场景 + 主角两条边界"，
        # 完全查不了 NPC 知识泄漏，而重抽稿和主稿一样可被采纳（2026-09-23 修）。
        # 走"补存"而不是"兜底"（用户定）：上一稿的 participants 就在候选文件里，直接读，
        # 不用"此刻在场"去猜——在场 ≠ 上缴过。
        merged = {pid for pid in (*result.participants, *latest.participants) if pid and pid != player}
        qc_participants = [player, *sorted(merged)]

        if self.settings.qc_enabled:
            await self._progress("qc", "质检员审校中")
            qc = await run_qc(
                self.llm,
                self.session.world,
                self.session.ledger,
                out.prose,
                preset=self.preset,
                reference=build_qc_reference(
                    self.session.world,
                    self.session.ledger,
                    qc_participants,
                    known_limit=self.settings.limits().known_set_limit,
                ),
                summary_hint=out.summary,
                temperature=self.settings.temp_qc,
            )
            prose = qc.prose
            issues = qc.issues
            summary_hint = qc.summary or out.summary
        else:
            prose = out.prose
            issues = []
            summary_hint = out.summary

        side_effects = latest.side_effects.model_copy(deep=True)
        if side_effects.narrative is not None:
            side_effects.narrative["summary"] = summary_hint or (prose or "")[:40]

        now = dt.datetime.now().isoformat(timespec="seconds")
        candidate = Candidate(
            candidate_id=new_id("cand"),
            turn_id=turn_id,
            trace_id=latest.trace_id,
            mode=mode,
            player_input=latest.player_input,
            writer_directive=latest.writer_directive,
            prose=prose,
            side_effects=side_effects,
            conflicts=issues + result.notes,
            participants=qc_participants,
            created_at=now,
            updated_at=now,
        )
        self.session.candidates.save(candidate)
        return candidate

    def discard(self, turn_id: str) -> None:
        self.transaction.discard_turn(turn_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _audit_callback(self, candidate):
        """Audit input adapter: run_audit takes prose + player input.

        时间与在场全由审计从正文+玩家输入推断，规则侧不再预结算，所以这里
        没有 settle_hint 要转达（2026-09-16 去规则化）。
        """
        return await run_audit(
            self.llm,
            self.session.world,
            self.session.ledger,
            candidate.prose,
            candidate.player_input or "",
            temperature=0.2,
        )

    async def adopt(self, candidate_id: str) -> None:
        await self.transaction.commit(candidate_id, audit=self._audit_callback)