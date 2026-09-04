from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.ledger.access import apply_access_override
from app.ledger.save import EntityRuntime

router = APIRouter(prefix="/api/sessions")


class DirectorBody(BaseModel):
    topic: str  # advice | qa | chat | discuss | backstage
    question: str = ""
    message: str = ""
    action: str = ""
    payload: dict = {}


def _get_session(request: Request, sid: str):
    session = request.app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    return session


@router.post("/{sid}/director")
async def director(request: Request, sid: str, body: DirectorBody):
    session = _get_session(request, sid)
    if body.topic == "advice":
        hooks = [h.model_dump() for h in session.ledger.save.hooks if h.status == "open"]
        return {"suggestions": [{"text": h["text"], "kind": "closing"} for h in hooks[:3]]}
    if body.topic == "qa":
        return {"answer": f"当前时间：{session.ledger.save.clock}"}
    if body.topic in {"chat", "discuss"}:
        return await _director_chat(request, session, body.message)
    if body.topic == "backstage":
        return _handle_backstage(session, body.action, body.payload)
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
    world = session.world

    presences = [
        world.npcs[pid].name if pid in world.npcs else pid
        for pid in session.ledger.present_at(session.ledger.save.player_scene)
    ]
    system = "\n".join(
        [
            "你是 AIWorld 的导演，玩家正在戏外和你讨论剧情走向。",
            "你不是正文执笔者，只负责建议、答疑、讨论剧情、幕后事务建议。",
            "世界概要（不可违背）：",
            *world.meta.summary,
            "当前时间：" + session.ledger.save.clock,
            "当前场景：" + session.ledger.save.player_scene,
            "在场 NPC：" + ("、".join(presences) if presences else "无"),
        ]
    )

    history = session.director_history[-20:]
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    reply = await llm.complete_text(
        messages,
        model=settings.resolved_model("director"),
        temperature=0.7,
    )
    session.director_history.append({"role": "user", "content": message})
    session.director_history.append({"role": "assistant", "content": reply})
    return {"reply": reply}


def _handle_backstage(session, action: str, payload: dict):
    ledger = session.ledger
    if action == "access_rejudge":
        event_id = payload.get("event_id")
        known_by = payload.get("known_by")
        if event_id not in ledger.by_id:
            raise HTTPException(status_code=404, detail=f"event not found: {event_id}")
        apply_access_override(ledger, event_id, known_by)
        return {"ok": True}

    if action == "force_actor":
        npc_id = payload.get("npc_id", "")
        forced = bool(payload.get("forced", True))
        if npc_id not in session.world.npcs:
            raise HTTPException(status_code=404, detail=f"npc not found: {npc_id}")
        entity = ledger.save.entities.setdefault(npc_id, EntityRuntime())
        entity.forced_actor = forced
        ledger.persist_save()
        return {"ok": True, "npc_id": npc_id, "forced_actor": forced}

    if action == "override":
        subject = payload.get("subject", "")
        location = payload.get("location", "")
        if not subject or not location:
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
        return {"ok": True, "event": event}

    if action == "amend_card":
        npc_id = payload.get("npc_id", "")
        persona_patch = payload.get("persona_patch")
        if npc_id not in session.world.npcs:
            raise HTTPException(status_code=404, detail=f"npc not found: {npc_id}")
        entity = ledger.save.entities.setdefault(npc_id, EntityRuntime())
        entity.persona_patch = persona_patch
        ledger.persist_save()
        return {"ok": True}

    raise HTTPException(status_code=400, detail=f"unknown backstage action: {action}")