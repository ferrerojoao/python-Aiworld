from __future__ import annotations

from app.ledger.queries import Ledger
from app.world.models import NarrativePreset, WorldContent
from app.workers.schemas import QCOutput


async def run_qc(
    llm,
    world: WorldContent,
    ledger: Ledger,
    prose: str,
    *,
    participants: list[str] | None = None,
    preset: NarrativePreset | None = None,
    model: str = "fake",
    temperature: float = 0.2,
) -> QCOutput:
    """QC worker: style check, leak check, banned words, conflicts."""
    preset = preset or world.presets
    banned = set(preset.banned_words)
    issues: list[dict] = []
    fixed = prose

    for word in banned:
        if word in fixed:
            fixed = fixed.replace(word, "……")
            issues.append({"level": "minor", "desc": f"禁用词替换: {word}"})

    system = "\n".join(
        [
            "你是 AIWorld 的质检员，负责文风、泄漏和禁用词检查。",
            "只返回 JSON，格式如下：",
            '{"status": "pass|fixed|conflict", "prose": "质检后的正文（可修改，不得为空）", "issues": [{"level": "minor|major", "desc": "问题描述"}]}',
            "status：pass=无需修改；fixed=已修改正文或替换禁用词；conflict=发现大矛盾（正文照常给出，矛盾只挂起不阻塞）。",
            "issues：发现的问题列表，没有则为空数组。",
        ]
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"正文：\n{fixed}\n\n请给出质检结果。"},
    ]

    data = await llm.complete_json(
        messages,
        QCOutput,
        model=model,
        temperature=temperature,
    )
    out = QCOutput.model_validate(data)
    if not out.prose:
        out.prose = fixed
    if issues:
        out.issues.extend(issues)
        out.status = "fixed"
    return out