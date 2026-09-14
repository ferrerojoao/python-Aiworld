from __future__ import annotations

from app.core.workorder import build_audit_work_order
from app.ledger.queries import Ledger
from app.world.models import WorldContent
from app.workers.schemas import AuditOutput


async def run_audit(
    llm,
    world: WorldContent,
    ledger: Ledger,
    prose: str,
    player_input: str,
    *,
    model: str = "fake",
    temperature: float = 0.2,
    settle_hint: str = "",
) -> AuditOutput:
    """Audit worker: infers world side effects from an adopted prose and
    judges goal completion / lifecycle.

    This is a pure inference step — it writes nothing; the transaction
    applies the returned AuditOutput (clock, narrative, scene registration,
    goals, lifecycle).

    ``settle_hint``：规则侧已算好的跳时绝对时刻。传给它，审计就不会把同一段
    跨度再估一遍（双重计费）。
    """
    system = build_audit_work_order(world, ledger, ledger.current_scene(), settle_hint)
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