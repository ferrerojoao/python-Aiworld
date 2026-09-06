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
        hooks = [h.model_dump() for h in session.ledger.save.hooks if h.status == "open"]
        return {"suggestions": [{"text": h["text"], "kind": "closing"} for h in hooks[:3]]}
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


def _write_npc_card(session, npc_id: str) -> None:
    """Persist one NPC card in the save's world instance and reload."""
    from app.core.store import write_json_atomic

    npc = session.world.npcs[npc_id]
    card_path = session.world_dir / "npcs" / f"{npc_id}.json"
    write_json_atomic(card_path, npc.model_dump())
    session.reload_world()


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

    if action_type == "amend_card":
        # 补卡事务：把增补内容写入存档世界实例的人物卡（人格增厚，玩家确认后）。
        npc_id = _resolve_npc_ref(session, payload.get("npc_id", ""))
        amendment = (payload.get("amendment") or "").strip()
        if npc_id is None:
            raise HTTPException(status_code=404, detail=f"npc not found: {payload.get('npc_id')}")
        if not amendment:
            raise HTTPException(status_code=400, detail="amendment is required")
        npc = session.world.npcs[npc_id]
        npc.persona = f"{npc.persona}\n{amendment}".strip()
        _write_npc_card(session, npc_id)
        return {"ok": True, "action": action_type}

    if action_type == "create_npc":
        # 角色转正：AI 现场捏的角色 → 玩家深聊 → 建档为实例 NPC 卡。
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        from app.world.models import NpcCard

        npc_id = f"npc_{payload.get('npc_id') or name[:8]}"
        while npc_id in session.world.npcs:
            npc_id += "_x"
        card = NpcCard(
            id=npc_id,
            name=name,
            appearance=(payload.get("appearance") or "").strip(),
            persona=(payload.get("persona") or "").strip() or f"名字：{name}。（作者尚未细写）",
            private_note=(payload.get("private_note") or "").strip() or None,
            personal_secrets=(payload.get("personal_secrets") or "").strip() or None,
            has_actor=bool(payload.get("has_actor", False)),
        )
        from app.core.store import write_json_atomic

        npcs_dir = session.world_dir / "npcs"
        npcs_dir.mkdir(exist_ok=True)
        write_json_atomic(npcs_dir / f"{npc_id}.json", card.model_dump())
        session.reload_world()
        return {"ok": True, "action": action_type, "npc_id": npc_id}

    if action_type == "add_scene":
        # 场景转正：玩家走进/提及包外地点 → 注册为实例场景节点，可复用。
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        from app.world.models import Scene

        scene_id = payload.get("scene_id") or f"scene_{name[:8]}"
        while scene_id in {s.id for s in session.world.scenes}:
            scene_id += "_x"
        scene = Scene(
            id=scene_id,
            name=name,
            aliases=payload.get("aliases") or [name],
            tags=payload.get("tags") or [],
            perceivable=payload.get("perceivable") or "这里看起来是个还没仔细描述的地方。",
            open_hours=payload.get("open_hours") or "全天",
            adjacent=payload.get("adjacent") or [session.ledger.save.player_scene],
        )
        from app.core.store import write_json_atomic

        scenes_path = session.world_dir / "scenes.json"
        scenes = [s.model_dump() for s in session.world.scenes] + [scene.model_dump()]
        write_json_atomic(scenes_path, scenes)
        session.reload_world()
        return {"ok": True, "action": action_type, "scene_id": scene_id}

    raise HTTPException(status_code=400, detail=f"unknown action: {action_type}")