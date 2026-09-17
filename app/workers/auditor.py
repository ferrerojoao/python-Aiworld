from __future__ import annotations

from app.core.workorder import build_audit_work_order
from app.ledger.queries import Ledger
from app.workers.schemas import AuditOutput
from app.world.models import WorldContent


async def run_audit(
    llm,
    world: WorldContent,
    ledger: Ledger,
    prose: str,
    player_input: str,
    *,
    model: str = "fake",
    temperature: float = 0.2,
) -> AuditOutput:
    """Audit worker: infers world side effects from an adopted prose and
    judges goal completion / lifecycle.

    This is a pure inference step — it writes nothing; the transaction
    applies the returned AuditOutput (clock, narrative, scene registration,
    goals, lifecycle).

    时间是审计的独家职责（2026-09-16）：规则侧不再预算，玩家输入随 user
    message 一并送达，所以“玩家给了具体时间就按玩家要求、没给就自行估计”
    无需额外的 ``settle_hint`` 通道。
    """
    system = build_audit_work_order(world, ledger, ledger.current_scene())
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"已采纳正文：\n{prose}\n\n玩家输入：{player_input}"},
    ]
    data = await llm.complete_json(
        messages,
        AuditOutput,
        model=model,
        temperature=temperature,
    )
    return AuditOutput.model_validate(data)