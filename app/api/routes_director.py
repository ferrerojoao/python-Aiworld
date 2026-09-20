from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.workorder import build_director_chat_system
from app.ledger.access import apply_access_override
from app.workers.schemas import DirectorAction, DirectorReply

router = APIRouter(prefix="/api/sessions")


class DirectorBody(BaseModel):
    topic: str  # chat | confirm | advice | qa | discuss (legacy)
    question: str = ""
    message: str = ""
    action: dict = {}


def _get_session(request: Request, sid: str):
    session = request.app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    return session


@router.post("/{sid}/director")
async def director(request: Request, sid: str, body: DirectorBody):
    session = _get_session(request, sid)
    if body.topic == "confirm":
        if not body.action:
            raise HTTPException(status_code=400, detail="action is required")
        try:
            action = DirectorAction.model_validate(body.action)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"bad action: {exc}") from exc
        return _execute_action(request, session, action.type, action.payload)
    if body.topic == "chat":
        return await _director_chat(request, session, body.message)
    # legacy topics kept for API compatibility (frontend uses chat only)
    if body.topic == "advice":
        goals = [g.model_dump() for g in session.ledger.save.goals if g.status == "active"]
        return {"suggestions": [{"text": g["text"], "kind": "goal"} for g in goals[:3]]}
    if body.topic == "qa":
        return {"answer": f"当前时间：{session.ledger.save.clock}"}
    if body.topic in {"discuss"}:
        return await _director_chat(request, session, body.message)
    raise HTTPException(status_code=400, detail="unknown topic")


@router.get("/{sid}/director/history")
async def director_history(request: Request, sid: str):
    session = _get_session(request, sid)
    return {"history": session.director_history}


async def _director_chat(request, session, message: str):
    if not message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    llm = request.app.state.llm
    settings = request.app.state.settings

    system = build_director_chat_system(
        session.world,
        session.ledger,
        session.ledger.current_scene(),
        limits=settings.limits(),
    )
    history = session.director_history[-20:]
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    out = None
    for _attempt in range(2):  # 导演回空话就地重试一次，仍空则给兜底话术
        data = await llm.complete_json(
            messages,
            DirectorReply,
            # 角色（= 模型名 + 出口，见 LLMRouter）；导演属主模型那一侧。
            worker="director",
            temperature=0.7,
        )
        out = DirectorReply.model_validate(data)
        if out.reply:
            break
    if out is None or not out.reply:
        out = DirectorReply(reply="（导演暂时没想出什么，稍后再聊。）")

    session.director_history.append({"role": "user", "content": message})
    session.director_history.append({"role": "assistant", "content": out.reply or ""})
    result: dict = {"reply": out.reply or ""}
    if out.action is not None:
        result["pending_action"] = out.action.model_dump()
    return result


def _resolve_npc_ref(session, ref: str) -> str | None:
    """Resolve an NPC reference to its id (= 中文名，如"朱明"）。"""
    ref = (ref or "").strip()
    return ref if ref in session.world.npcs else None


def _execute_action(request, session, action_type: str, payload: dict) -> dict:
    """Execute a confirmed backstage action (player already confirmed it)."""
    ledger = session.ledger
    if action_type == "access_rejudge":
        event_id = payload.get("event_id")
        known_by = payload.get("known_by")
        if event_id not in ledger.by_id:
            raise HTTPException(status_code=404, detail=f"event not found: {event_id}")
        apply_access_override(ledger, event_id, known_by)
        return {"ok": True, "action": action_type}

    if action_type == "inject_memory":
        # 记忆注入（M16）：落一条私密事件 known_by=[该NPC]，只进他自己的切片。
        npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
        memory = (payload.get("memory") or "").strip()
        if npc_id is None:
            raise HTTPException(status_code=404, detail=f"npc not found: {payload.get('npc_id')}")
        if not memory:
            raise HTTPException(status_code=400, detail="memory is required")
        event = {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": ledger.save.clock,
            "location": None,
            "participants": [npc_id],
            "known_by": [npc_id],
            "body": memory,
            "summary": memory[:60],
            "player_input": None,
            "source": "director",
        }
        ledger.append(event)
        ledger.persist_save()
        return {"ok": True, "action": action_type, "event": event, "npc": npc_id}

    if action_type == "set_goal":
        # 剧情目标（M14）：设立 / 废弃（玩家确认后落账）。
        from app.ledger.goals import abandon_goal, add_goal

        if payload.get("status") == "abandoned":
            goal_id = payload.get("goal_id", "")
            if not goal_id:
                raise HTTPException(status_code=400, detail="goal_id is required")
            goal = abandon_goal(ledger, goal_id)
            if goal is None:
                raise HTTPException(status_code=404, detail=f"active goal not found: {goal_id}")
            ledger.persist_save()
            return {"ok": True, "action": action_type, "goal_id": goal_id}
        text = (payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        npc_id = payload.get("npc_id") or None
        if npc_id:
            npc_id = _resolve_npc_ref(session, npc_id)
            if npc_id is None:
                raise HTTPException(status_code=404, detail=f"npc not found: {payload.get('npc_id')}")
        try:
            goal = add_goal(
                ledger,
                text=text,
                kind=payload.get("kind") or "small",
                subject=payload.get("subject") or "",
                big_goal_id=payload.get("big_goal_id") or None,
                npc_id=npc_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ledger.persist_save()
        return {"ok": True, "action": action_type, "goal": goal.model_dump()}

    if action_type == "retire":
        # 角色退场：玩家确认后打 lifecycle 标（不可逆），并落一条导演留痕事件。
        npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
        if npc_id is None:
            raise HTTPException(status_code=404, detail=f"npc not found: {payload.get('npc_id')}")
        # 主角不可退场（2026-09-13）：主角已入人物表，退场等于把主角从世界注销。
        if npc_id == session.world.player_name():
            raise HTTPException(
                status_code=400, detail=f"{npc_id}是主角，不能退场（改主角走世界工作台的人物卡）"
            )
        from app.ledger.save import EntityRuntime

        entity = ledger.save.entities.setdefault(npc_id, EntityRuntime())
        entity.lifecycle = "retired"
        event = {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": ledger.save.clock,
            "location": None,
            "participants": [npc_id],
            "known_by": sorted({npc_id, session.world.player_name()}),
            "body": "",
            "summary": f"{npc_id}退场（永久离开舞台）",
            "player_input": None,
            "source": "director",
        }
        ledger.append(event)
        ledger.persist_save()
        return {"ok": True, "action": action_type, "event": event, "npc": npc_id}

    if action_type == "override":
        subject = _resolve_npc_ref(session, payload.get("subject", ""))
        location = payload.get("location", "")
        if subject is None or not location:
            raise HTTPException(status_code=400, detail="subject and location are required")
        subject_name = subject
        location_name = location
        event = {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": ledger.save.clock,
            "location": location,
            "participants": [subject],
            "known_by": None,
            "body": f"{subject_name}在{location_name}。",
            "source": "director",
        }
        ledger.append(event)
        ledger.persist_save()
        return {"ok": True, "action": action_type, "event": event}

    if action_type == "state_add":
        # 状态新增（Step 2c）：玩家明确要求给某人加一条长期事实。
        # **刻意不做语义门槛**——审计侧那套"只收长期事实"的判据是防它自己过度
        # 发挥的；玩家已经拍板的事，再拿语义闸拦人很荒谬（见 Transaction.add_state）。
        # 走 runner 而不是直接改 ledger：这样记账事件、last_state_change.added
        # 与左侧栏的即时撤销入口都自动跟上（与"玩家撤销"对称）。
        runner = request.app.state.turn_runner_factory(session)
        npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
        if npc_id is None:
            raise HTTPException(
                status_code=404, detail=f"npc not found: {payload.get('npc_id')}"
            )
        try:
            item = runner.transaction.add_state(
                npc_id,
                str(payload.get("text") or ""),
                until=str(payload.get("until") or ""),
                public=bool(payload.get("public")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "action": action_type, "npc": npc_id, "state": item.model_dump()}

    if action_type == "state_revoke":
        # 状态撤销（Step 2c）：优先按 state_id 精确定位（导演看得见清单里的 id）；
        # 抄错 / 没给 id 时退而用 {npc_id, text} 做**精确文本**匹配——不做模糊匹配，
        # "骨裂"与"左臂骨裂"必须分得开。两条路都走同一个 revoke_state：
        # 留痕形状、对冲事件、"（玩家撤销）"文案与抽屉里的 × 完全一致。
        runner = request.app.state.turn_runner_factory(session)
        state_id = str(payload.get("state_id") or payload.get("id") or "").strip()
        if not state_id:
            npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
            text = str(payload.get("text") or "").strip()
            if npc_id is None or not text:
                raise HTTPException(
                    status_code=400,
                    detail="state_id 或 {npc_id, text} 至少给一个",
                )
            hit = next(
                (
                    it
                    for name, runtime in ledger.save.entities.items()
                    if name == npc_id
                    for it in runtime.states
                    if (it.text or "").strip() == text
                ),
                None,
            )
            if hit is None:
                raise HTTPException(
                    status_code=404, detail=f"{npc_id} 没有这条状态：{text}"
                )
            state_id = hit.id
        if not runner.transaction.revoke_state(state_id):
            raise HTTPException(
                status_code=404, detail=f"状态不存在（可能已被撤销）：{state_id}"
            )
        return {"ok": True, "action": action_type, "state_id": state_id}

    if action_type == "state_revise":
        # 状态修订（2026-09-16）：原地改一条状态的字段，**不换条目**——id / since /
        # source_event 全保留，所以"他什么时候起就是这样的"不会被改错字重置。
        # 与 state_revoke 的关键差别：它只认 state_id，**不做 {npc_id, text} 退路**
        # ——那个退路在这里有歧义（text 到底是"要改的那条"还是"改成什么"），
        # 猜错就是改错一条事实。导演工单里的清单逐条带方括号 id，照抄即可。
        runner = request.app.state.turn_runner_factory(session)
        state_id = str(payload.get("state_id") or payload.get("id") or "").strip()
        if not state_id:
            raise HTTPException(
                status_code=400,
                detail="state_revise 需要 state_id（上方清单里的方括号 id，逐字照抄）",
            )
        # 「有没有给这个字段」与「给了什么值」必须分开：until 给空串是"清掉期限"
        # （合法意图），而**不给** = 这一项不动。所以判定看键在不在 payload 里。
        kwargs: dict = {}
        if "text" in payload:
            kwargs["text"] = str(payload.get("text") or "")
        if "until" in payload:
            kwargs["until"] = str(payload.get("until") or "")
        if "public" in payload:
            kwargs["public"] = bool(payload.get("public"))
        try:
            item = runner.transaction.revise_state(state_id, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail=f"状态不存在：{state_id}")
        return {
            "ok": True,
            "action": action_type,
            "state_id": item.id,
            "state": item.model_dump(),
        }

    # 导演窗口只管改账本（覆写/记忆注入/改判/剧情目标/退场/状态增撤改）。模型越权
    # 提议世界资产动作时，给玩家一句人话并指向正确的入口，而不是裸 400。
    raise HTTPException(
        status_code=400,
        detail=(
            f"导演窗口不支持「{action_type}」这个动作。"
            "改账本的事（覆写 / 记忆注入 / 访问改判 / 剧情目标 / 角色退场 / 状态增删）"
            "在这里确认后执行；"
            "人物卡、转正、场景、世界书等世界资产请到「世界工作台」直接编辑。"
        ),
    )