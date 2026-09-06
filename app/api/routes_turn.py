from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.runtime.trace import TraceRecorder
from app.runtime.turn import NeedChooseCandidate, TurnRunner

router = APIRouter(prefix="/api/sessions")


class TurnBody(BaseModel):
    input: str
    adopt_candidate_id: str | None = None


class RerollBody(BaseModel):
    mode: str = "rephrase"
    note: str = ""


def _get_session(request: Request, sid: str):
    session = request.app.state.sessions.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session not found: {sid}")
    return session


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{sid}/turn")
async def post_turn(request: Request, sid: str, body: TurnBody):
    session = _get_session(request, sid)
    # Wrap the real LLM with a trace recorder for this turn.
    trace = TraceRecorder(request.app.state.llm)
    runner = TurnRunner(
        session,
        trace,
        request.app.state.settings,
        preset=request.app.state.global_preset,
    )
    session.debug_trace = trace.entries

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        runner.set_progress_queue(queue)
        before = trace.get_usage()
        cache_before = session.ledger.cache_stats_snapshot()

        async def run():
            try:
                if body.adopt_candidate_id:
                    # The frontend sends the currently displayed candidate as the
                    # default to adopt before processing the next input.
                    await runner.adopt(body.adopt_candidate_id)
                candidate = await runner.run_turn(body.input)
                after = trace.get_usage()
                cache_after = session.ledger.cache_stats_snapshot()
                settings = request.app.state.settings
                await queue.put(
                    {
                        "type": "candidate",
                        "data": {
                            "trace_id": candidate.trace_id,
                            "turn_id": candidate.turn_id,
                            "candidate_id": candidate.candidate_id,
                            "prose": candidate.prose,
                            "side_effects": candidate.side_effects.model_dump(),
                            "conflicts": candidate.conflicts,
                            "usage": {
                                "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
                                "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
                                "total_tokens": after["total_tokens"] - before["total_tokens"],
                                "calls": after["calls"] - before["calls"],
                            },
                            "cache": {
                                "hits": cache_after["hits"] - cache_before["hits"],
                                "misses": cache_after["misses"] - cache_before["misses"],
                            },
                            "model": settings.resolved_model("story"),
                            "reasoning_effort": settings.reasoning_effort or "auto",
                        },
                    }
                )
            except NeedChooseCandidate as exc:
                await queue.put({"type": "error", "data": {"message": str(exc)}})
            except Exception as exc:  # noqa: BLE001
                await queue.put(
                    {"type": "error", "data": {"message": f"{type(exc).__name__}: {exc}"}}
                )

        task = asyncio.create_task(run())
        while True:
            item = await queue.get()
            if item["type"] == "stage":
                yield _sse(
                    "stage",
                    {"stage": item.get("stage"), "label": item.get("label")},
                )
            elif item["type"] == "candidate":
                yield _sse("candidate", item["data"])
                break
            elif item["type"] == "error":
                yield _sse("error", item["data"])
                break
        task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{sid}/candidates/pending")
async def pending_candidates(request: Request, sid: str):
    session = _get_session(request, sid)
    return {
        "candidates": [
            c.model_dump()
            for c in session.candidates.list_pending()
        ]
    }


@router.get("/{sid}/debug/latest")
async def debug_latest(request: Request, sid: str):
    session = _get_session(request, sid)
    return {"trace": session.debug_trace}


@router.post("/{sid}/candidates/{candidate_id}/adopt")
async def adopt_candidate(request: Request, sid: str, candidate_id: str):
    session = _get_session(request, sid)
    runner = request.app.state.turn_runner_factory(session)
    try:
        await runner.adopt(candidate_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{sid}/turns/{turn_id}/reroll")
async def reroll_turn(request: Request, sid: str, turn_id: str, body: RerollBody):
    session = _get_session(request, sid)
    runner = request.app.state.turn_runner_factory(session)
    try:
        candidate = await runner.reroll(turn_id, mode=body.mode, note=body.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return candidate.model_dump()


@router.post("/{sid}/turns/{turn_id}/discard")
async def discard_turn(request: Request, sid: str, turn_id: str):
    session = _get_session(request, sid)
    runner = request.app.state.turn_runner_factory(session)
    runner.discard(turn_id)
    return {"ok": True}