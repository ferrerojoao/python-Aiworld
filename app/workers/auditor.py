from __future__ import annotations

from app.ledger.hooks import scan_hooks
from app.ledger.queries import Ledger
from app.ledger.save import EntityRuntime
from app.workers.schemas import AuditOutput


async def run_audit(
    llm,
    ledger: Ledger,
    narrative_event: dict,
    *,
    model: str = "fake",
    temperature: float = 0.2,
) -> AuditOutput:
    """Audit worker: post-commit settlement.

    In v1 this runs synchronously inside the adopt request. It can scan
    hooks and detect simple lifecycle changes.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是 AIWorld 的世界审计，负责从事件中提取定性附注、矛盾和 NPC 生命周期变化。只返回 JSON，格式如下：\n"
                '{"hook_texts": ["值得挂账的承诺/未了事"], "conflicts": [{"level": "major", "desc": "矛盾描述"}], "lifecycle": [{"npc_id": "npc_zhuming", "status": "retired"}]}\n'
                "hook_texts：没有则为空数组；conflicts：没有则为空数组。\n"
                "lifecycle：只有某 NPC 本人真正死亡或永久离开这个世界时才标记 retired，且必须给出其 npc_id；没有则为空数组。"
                "注意：游戏/故事内的虚构内容（如游戏角色死亡、口误、玩笑、转述）不算真实退场，不要标记。"
            ),
        },
        {"role": "user", "content": f"事件：{narrative_event}"},
    ]
    data = await llm.complete_json(
        messages,
        AuditOutput,
        model=model,
        temperature=temperature,
    )
    out = AuditOutput.model_validate(data)
    scan_hooks(ledger, narrative_event)
    for item in out.lifecycle or []:
        npc_id = str(item.get("npc_id") or item.get("id") or "")
        status = str(item.get("status") or "")
        if npc_id in ledger.world.npcs and status == "retired":
            entity = ledger.save.entities.setdefault(npc_id, EntityRuntime())
            entity.lifecycle = "retired"
            ledger.persist_save()
    return out