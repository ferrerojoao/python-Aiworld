from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.workorder import build_director_chat_system
from app.ledger.access import apply_access_override
from app.ledger.save import EntityRuntime
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
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"bad action: {exc}") from exc
        return _execute_action(session, action.type, action.payload)
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
        session.world, session.ledger, session.ledger.save.player_scene
    )
    history = session.director_history[-20:]
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    out = None
    for attempt in range(2):
        data = await llm.complete_json(
            messages,
            DirectorReply,
            model=settings.resolved_model("director"),
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
    """Resolve 'npc_zhuming' or '朱明' to the NPC's id (model-friendly)."""
    ref = (ref or "").strip()
    if ref in session.world.npcs:
        return ref
    for pid, card in session.world.npcs.items():
        if card.name == ref:
            return pid
    return None


def _execute_action(session, action_type: str, payload: dict) -> dict:
    """Execute a confirmed backstage action (player already confirmed it)."""
    ledger = session.ledger
    if action_type == "access_rejudge":
        event_id = payload.get("event_id")
        known_by = payload.get("known_by")
        if event_id not in ledger.by_id:
            raise HTTPException(status_code=404, detail=f"event not found: {event_id}")
        apply_access_override(ledger, event_id, known_by)
        return {"ok": True, "action": action_type}

    if action_type == "set_actor":
        # 角色档位：直接写存档世界实例的人物卡（设计 C，玩家判断）。
        npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
        if npc_id is None:
            raise HTTPException(status_code=404, detail=f"npc not found: {payload.get('npc_id')}")
        has = bool(payload.get("has_actor", True))
        session.world.npcs[npc_id].has_actor = has
        _write_npc_card(session, npc_id)
        return {"ok": True, "action": action_type, "npc_id": npc_id, "has_actor": has}

    if action_type == "inject_memory":
        # 记忆注入（M16）：落一条私密事件 known_by=[该NPC]，只进他自己的切片。
        npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
        memory = (payload.get("memory") or "").strip()
        if npc_id is None:
            raise HTTPException(status_code=404, detail=f"npc not found: {payload.get('npc_id')}")
        if not memory:
            raise HTTPException(status_code=400, detail="memory is required")
        npc = session.world.npcs.get(npc_id)
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
        return {"ok": True, "action": action_type, "event": event, "npc": npc.name if npc else npc_id}

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
                subject=payload.get("subject") or "player",
                big_goal_id=payload.get("big_goal_id") or None,
                npc_id=npc_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ledger.persist_save()
        return {"ok": True, "action": action_type, "goal": goal.model_dump()}

    if action_type == "override":
        subject = _resolve_npc_ref(session, payload.get("subject", ""))
        location = payload.get("location", "")
        if subject is None or not location:
            raise HTTPException(status_code=400, detail="subject and location are required")
        npc = session.world.npcs.get(subject)
        scene = next((s for s in session.world.scenes if s.id == location), None)
        subject_name = npc.name if npc else subject
        location_name = scene.name if scene else location
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

    raise HTTPException(status_code=400, detail=f"unknown action: {action_type}")