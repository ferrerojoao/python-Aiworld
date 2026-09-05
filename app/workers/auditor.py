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
                "你是 AIWorld 的世界审计，负责从事件中提取定性附注和矛盾。只返回 JSON，格式如下：\n"
                '{"hook_texts": ["值得挂账的承诺/未了事"], "conflicts": [{"level": "major", "desc": "矛盾描述"}]}\n'
                "hook_texts：没有则为空数组；conflicts：没有则为空数组。"
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
    _scan_lifecycle(ledger, narrative_event)
    return out


def _scan_lifecycle(ledger: Ledger, narrative_event: dict) -> None:
    body = narrative_event.get("body", "")
    if not any(k in body for k in ["死了", "去世", "永久离开", "离开了青石镇"]):
        return
    for npc_id, npc in ledger.world.npcs.items():
        if npc.name in body:
            entity = ledger.save.entities.setdefault(npc_id, EntityRuntime())
            entity.lifecycle = "retired"