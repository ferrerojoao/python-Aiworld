"""大总结起草：把待浓缩的事件写成一份编年史（2026-10-10）。

与落卡草稿（``app/workers/landing.py``）同一条纪律：**输入只有事件本身**——不喂
世界书、不喂人物卡底牌、不喂别的世界的任何东西。所以模型写出来的每一句都能在
事件流里对得上账。

**滚动重压**：``previous`` 是上一条编年史的**正文全文**，它要被**并进**新正文——
既不是"另行留存"，也不是"接着往下写"。喂全文而不是喂"摘要的摘要"，是"越总结越空"
的解药。

🔴 **2026-10-12 实机教训**：原提示词写的是「在它基础上**续写**：旧事不再展开」，
模型（deepseek-flash）把这句读成"别写旧的"，交回来的新编年史以 **「（承前）」** 开头、
旧内容**一条没有**——而旧那条已被替换掉，信息真丢了。**改稿时绝不许把"别写旧的"
这类话再写回来**：提示词必须是"合并重写、旧事可以更简但**不许整段消失**、正文
**自包含**"。这一点由
``tests/test_digest.py::test_final_prompt_forces_a_self_contained_merge`` 钉住。

**超长**：原料超过 ``CHUNK_CHARS`` 就先分段各压一遍（map），再拿段落要点合成
（reduce）。只做一层——大总结的原料是"上一次总结 + 此后新增"，本来就短。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# 编年史的正文上限（字）。它是往后每一轮都注入的东西，必须有界；「越早越简」
# 就是在这个上限内分配详略的手段。
DIGEST_MAX_CHARS = 1000
# 单次送模型的原料上限（字）。超过就先分段。
CHUNK_CHARS = 4000


class DigestDraft(BaseModel):
    summary: str = ""
    text: str = ""


def _render_one(event: dict[str, Any]) -> str:
    head = f"{event.get('at') or '-'} · {event.get('location') or '-'}"
    body = (event.get("body") or "").strip() or (event.get("summary") or "").strip()
    return f"{head}\n{body}" if body else head


def _render(events: list[dict[str, Any]]) -> str:
    return "\n\n".join(_render_one(ev) for ev in events)


def _chunk(events: list[dict[str, Any]], budget: int = CHUNK_CHARS) -> list[list[dict[str, Any]]]:
    """按事件边界切段（不切开单条事件——切开的事件没有意义）。"""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for event in events:
        text = _render_one(event)
        if current and size + len(text) > budget:
            chunks.append(current)
            current = []
            size = 0
        current.append(event)
        size += len(text)
    if current:
        chunks.append(current)
    return chunks


_FINAL_SYSTEM = f"""你是这个世界的编年史官。把"已经发生过的事"浓缩成一份编年史，供此后继续游玩时当背景。

只输出 JSON：{{"summary": "一句话摘要（不超过 40 字，进事件日志用）", "text": "编年史正文"}}

纪律：

- 按时间顺序写，**越早的越简**：久远的事一两笔带过，近期的事保留细节。
- **只写确实发生过的事**。不编、不推测、不补前因后果、不评价。
- **留下仍在影响当下的东西**：谁退场/身故了、谁与谁结下了什么、主角身上留下了什么、
  目标推进到了哪一步、有什么还悬着。
- 🔴 有"已有编年史"时，这是一次**合并重写**——不是"接着往下写"。把已有编年史里的事
  **并进**新正文：旧事可以压得更简，但**一件事都不许整段消失**；篇幅不够时先压更早的，
  别把旧的一整段砍掉。
- 🔴 **正文必须自包含**：读者只看这一份。不许写"（承前）""如前""见上文""此前已述"
  这类指代，也不许指望读者去看你上一份编年史。
- 直接就是编年史正文，不写"以上""总之""本总结"这类元话术。
- 正文不超过 {DIGEST_MAX_CHARS} 字。"""

_SHARD_SYSTEM = """你是这个世界的编年史官。下面是从一整段历史里切出来的**其中一段**，
请把它浓缩成要点，供随后合成完整编年史用。

只输出 JSON：{"text": "这一段发生了什么的要点"}

纪律：

- 只写这一段里确实发生过的事，不编、不推测。
- 按时间顺序，越早越简。
- 保留仍在影响后面的东西（谁退场了、谁与谁的关系变了、留下了什么、目标推进到哪）。
- 直接写要点，不写"这一段""以上"这类元话术。
- 不超过 300 字。"""


def _final_messages(previous: str, material: str) -> list[dict[str, str]]:
    if previous.strip():
        head = (
            "（以下是**已有编年史**，它**即将被这一份新编年史取代**——请把它记得的事"
            f"全部并进新正文，可以压得更简，但不许整段丢掉）\n{previous.strip()}"
        )
    else:
        head = "（还没有编年史，这是第一份）"
    return [
        {"role": "system", "content": _FINAL_SYSTEM},
        {
            "role": "user",
            "content": (
                f"{head}\n\n"
                "（这是此后发生的事，从早到晚——要和上面的已有编年史**合并**成一份新的）\n"
                f"{material}"
            ),
        },
    ]


def _shard_messages(index: int, total: int, material: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SHARD_SYSTEM},
        {"role": "user", "content": f"（第 {index} / {total} 段）\n{material}"},
    ]


async def run_digest(
    llm,
    previous: str,
    events: list[dict[str, Any]],
    *,
    model: str = "",
    worker: str = "digest",
    temperature: float = 0.3,
) -> dict[str, Any]:
    """``(已有编年史, 待浓缩事件)`` → ``{summary, text, chunks, event_count}``。

    **不给 LLM 决定"能不能总结"**：没有事件时调用方就该拦在前头（那是代码判的
    事）。这里 ``events`` 为空也能跑（会得到一份空编年史），但不该被走到。
    """
    chunks = _chunk(events)
    if len(chunks) <= 1:
        material = _render(events) or "（没有任何事件）"
        data = await llm.complete_json(
            _final_messages(previous, material),
            DigestDraft,
            model=model,
            temperature=temperature,
            worker=worker,
        )
        draft = DigestDraft.model_validate(data)
        return {
            "summary": draft.summary.strip(),
            "text": draft.text.strip(),
            "chunks": 1,
            "event_count": len(events),
        }

    parts: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        data = await llm.complete_json(
            _shard_messages(index, len(chunks), _render(chunk)),
            DigestDraft,
            model=model,
            temperature=temperature,
            worker=worker,
        )
        parts.append(DigestDraft.model_validate(data).text.strip())
    data = await llm.complete_json(
        _final_messages(previous, "\n\n".join(parts)),
        DigestDraft,
        model=model,
        temperature=temperature,
        worker=worker,
    )
    draft = DigestDraft.model_validate(data)
    return {
        "summary": draft.summary.strip(),
        "text": draft.text.strip(),
        "chunks": len(chunks),
        "event_count": len(events),
    }
