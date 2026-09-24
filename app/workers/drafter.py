"""L2 一句话草稿：把玩家的一句设定要求扩成一个可玩的内容包草稿。

只产出**提案**：本模块返回的 assets 落盘与否由调用方决定，且必须过
``validate_assets``（临时目录试写 + ``check_world``）。校验不过时把问题清单
回灌给模型修正一次——「模型自评」不如「引擎体检」可靠，所以闸门在引擎侧。
"""

from __future__ import annotations

from app.world.draft import WorldDraft, draft_to_assets, validate_assets

_SYSTEM = """你是 AIWorld 的世界设定师：把玩家给的一句设定要求，扩成一个可直接开局的微型世界内容包。

输出一个 JSON 对象，字段如下（全部视为必填，缺项按下面的条数给足）：

{
  "name": "世界名（中文，如 青石镇）",
  "summary": ["世界概要，3~6 条，每条一句话", "给编剧看的背景块：地理、时代、气氛、这个世界的规矩"],
  "opening": "开局白，150~300 字。用第二人称写主角开场那一刻，地点必须落在 start_scene，最后停在主角可行动的一拍",
  "start_time": "世界钟起点，ISO 形式如 2026-07-14T08:00:00；拿不准就留空串",
  "start_scene": "开局场景名，必须与 scenes 里某一条的 id 逐字一致",
  "player_name": "主角真名（中文姓名）",
  "player_appearance": "主角外貌，一句话",
  "player_persona": "主角身份与性格，两三句话（编剧靠它决定主角能做什么、别人怎么看他）",
  "scenes": [{"id": "场景中文名", "aliases": ["别名"], "perceivable": "这个场景能被一眼看到、闻到、听到什么，一两句，以句号收尾（引擎把它直接拼进场景描述，所以它本身要是一段完整的话）", "region": "消息域，**必填、不许留空**：整个世界就是一座城/镇（场景都在同一片地方）→ 填 全域；有明显跨区域的知识边界（如 县城 / 乡下 / 码头各一片）→ 填那片地域的名字"}],
  "npcs": [{"id": "角色中文名", "appearance": "外貌一句话", "persona": "身份+性格+与主角的关系，两三句", "personal_secrets": "他自己知道、不愿对外说的心事；没有就留空串", "private_note": "连他自己都不知道的真相；没有就留空串", "has_actor": true}],
  "lorebook": [{"id": "条目名", "keywords": ["触发词1", "触发词2"], "body": "命中后给编剧的全文，两三句", "always_on": false}]
}

数量与命名约定：

- scenes：3~5 个。场景名是中文地点名（如 主街、鱼市、朱明家），它同时就是事件日志里的 location 与显示名，所以取具体、能一口叫出的名字。别名给玩家可能用的变体叫法（如 码头鱼市）。主角日常出没的地方要齐——家、常去的公共场所、一两个剧情场所。
- scenes[].region：**一个都不许留空**。留空在引擎里的意思是"这里的公开事件谁都不风闻"（那是给玩家手动表达"这事别外传"用的），整包都留空 = 这个世界彻底失声，NPC 之间再不传递任何消息。所以：整个世界就是一座城/镇 → 每个场景都填 `全域`；有明显跨区域的知识边界 → 按那片地域填（同一个地域在各场景里必须用**同一个名字**，差一个字就传不过去）。
- npcs：2~4 个（不含主角，主角由 player_* 单独给）。每个都给中文姓名或至少一个称呼。`has_actor` 给真正会跟主角深聊、会做抉择的 1~2 个角色填 true；纯背景角色填 false。
- lorebook：2~4 条。每条给 2~4 个关键词——玩家提到这些词时这段设定才进本回合上下文。keywords 按"玩家会怎么提起它"来给（地名、物名、称呼）。
- 地名、人名、物品名一律用中文，与 summary 里写的世界观自洽；同一个概念在不同字段里用同一个名字。

纪律：

- 你写的是**提案**，玩家看过、改过才落盘；所以按上面条数把包给足，让玩家有东西可改。
- 主角与 NPC 用同一套字段，但在输出里分开给（主角走 player_*，NPC 走 npcs），引擎落盘时才会把主角合成人物表里的 is_player 卡。
- 只输出 JSON 对象本身。"""


def _user_prompt(premise: str, name: str, player_name: str) -> str:
    lines = [f"设定要求：{premise.strip()}"]
    if name.strip():
        lines.append(f"世界名（玩家已指定，逐字沿用）：{name.strip()}")
    if player_name.strip():
        lines.append(f"主角名（玩家已指定，逐字沿用）：{player_name.strip()}")
    return "\n".join(lines)


async def run_world_draft(
    llm,
    premise: str,
    *,
    world_id: str,
    name: str = "",
    player_name: str = "",
    model: str = "",
    worker: str = "story",
    temperature: float = 1.0,
) -> tuple[WorldDraft, dict, list[str]]:
    """一句前提 → (草稿, 资产形状, 校验问题)。

    校验问题为空 = 可以落盘；非空 = 修正一次后仍不合格，调用方如实转告玩家。
    """
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(premise, name, player_name)},
    ]
    data = await llm.complete_json(
        messages, WorldDraft, model=model, temperature=temperature, worker=worker
    )
    draft = WorldDraft.model_validate(data)
    assets = draft_to_assets(draft, world_id, name=name, player_name=player_name)
    problems = validate_assets(assets)
    if not problems:
        return draft, assets, []

    # 一次修正：把引擎体检出的问题原文回灌（与 complete_json 的 schema 重试同构，
    # 但这里处理的是 check_world 的语义问题——关键词为空、主角名撞占位名之类，
    # Pydantic 看得见形状、看不见语义）。
    retry_messages = [
        *messages,
        {"role": "assistant", "content": draft.model_dump_json()},
        {
            "role": "user",
            "content": "上一版草稿没通过引擎校验，请按问题清单修正后重新输出完整 JSON：\n"
            + "\n".join(f"- {p}" for p in problems),
        },
    ]
    data = await llm.complete_json(
        retry_messages, WorldDraft, model=model, temperature=temperature, worker=worker
    )
    draft = WorldDraft.model_validate(data)
    assets = draft_to_assets(draft, world_id, name=name, player_name=player_name)
    return draft, assets, validate_assets(assets)
