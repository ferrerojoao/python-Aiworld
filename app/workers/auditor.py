from __future__ import annotations

from app.ledger.hooks import add_hooks, close_hooks
from app.ledger.queries import Ledger
from app.ledger.save import EntityRuntime
from app.workers.schemas import AuditOutput


async def run_audit(
    llm,
    ledger: Ledger,
    narrative_event: dict,
    *,
    hook_limit: int = 20,
    model: str = "fake",
    temperature: float = 0.2,
) -> AuditOutput:
    """Audit worker: post-commit settlement (runs synchronously on adopt).

    Handles three things per adopted event:
    - hooks: extracts new promises / unresolved matters (LLM), fulfils open
      hooks the event actually closes (LLM by id), enforces the open-hook
      cap with FIFO expiry (no due-date parsing).
    - lifecycle: NPC retirement from terminal events (LLM semantics).
    """
    open_hooks = [h for h in ledger.save.hooks if h.status == "open"]
    hooks_list = "\n".join(f"- [{h.id}] {h.text[:80]}" for h in open_hooks) or "（无）"
    messages = [
        {
            "role": "system",
            "content": (
                "你是 AIWorld 的世界审计，在玩家采纳事件后做结算。只返回 JSON，格式如下：\n"
                '{"hook_texts": ["本次对话冒出的承诺/未了事（玩家视角的一句话）"],'
                ' "closed_hook_ids": ["本次事件实际兑现/完成的钩子 id"],'
                ' "lifecycle": [{"npc_id": "npc_zhuming", "status": "retired"}]}\n'
                "hook_texts：没有则为空数组；每条必须是清晰独立的承诺/未了事，不要收录寒暄、约定俗成或已完成的话。\n"
                "closed_hook_ids：从下方开放钩子列表里挑出本次事件兑现的钩子 id；没有则为空数组。\n"
                "lifecycle：只有某 NPC 本人真正死亡或永久离开这个世界时才标记 retired，且必须给出其 npc_id；没有则为空数组。"
                "注意：游戏/故事内的虚构内容（如游戏角色死亡、口误、玩笑、转述）不算真实退场，不要标记。"
            ),
        },
        {
            "role": "user",
            "content": f"事件：{narrative_event}\n\n当前开放钩子（可兑现列表）：\n{hooks_list}",
        },
    ]
    data = await llm.complete_json(
        messages,
        AuditOutput,
        model=model,
        temperature=temperature,
    )
    out = AuditOutput.model_validate(data)

    add_hooks(ledger, out.hook_texts, event=narrative_event, limit=hook_limit)
    close_hooks(ledger, out.closed_hook_ids)
    for item in out.lifecycle or []:
        npc_id = str(item.get("npc_id") or item.get("id") or "")
        status = str(item.get("status") or "")
        if npc_id in ledger.world.npcs and status == "retired":
            entity = ledger.save.entities.setdefault(npc_id, EntityRuntime())
            entity.lifecycle = "retired"
    ledger.persist_save()
    return out