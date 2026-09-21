from __future__ import annotations

import json
from pathlib import Path

from app.core.store import write_json_atomic
from app.rules.lorebook import LORE_SUBJECT_CAP

from .models import (
    PLAYER_PLACEHOLDER,
    LoreEntry,
    NarrativePreset,
    NpcCard,
    Scene,
    WorldContent,
    WorldInfo,
)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_world(root: str | Path) -> WorldContent:
    """Load a content package from a directory.

    Missing optional files are treated as empty/default values. Required
    fields are validated through Pydantic.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"world content directory not found: {root}")

    raw_world = _read_json(root / "world.json", {})
    meta = WorldInfo.model_validate(raw_world)

    raw_lore = _read_json(root / "lorebook.json", [])
    lorebook = [LoreEntry.model_validate(item) for item in raw_lore]

    raw_scenes = _read_json(root / "scenes.json", [])
    scenes = [Scene.model_validate(item) for item in raw_scenes]

    npcs: dict[str, NpcCard] = {}
    npc_dir = root / "npcs"
    if npc_dir.is_dir():
        for path in sorted(npc_dir.glob("*.json")):
            card = NpcCard.model_validate(_read_json(path, {}))
            npcs[card.id] = card
    # 主角是人物表里的普通一员（2026-09-13）：世界包没写主角卡时合成一张占位卡，
    # 保证 world.player() 恒有返回值——引擎处处以"主角名"为键，缺卡会静默失配。
    if not any(card.is_player for card in npcs.values()):
        npcs[PLAYER_PLACEHOLDER] = NpcCard(id=PLAYER_PLACEHOLDER, is_player=True)

    raw_presets = _read_json(root / "presets.json", {})
    presets = NarrativePreset.model_validate(raw_presets)

    return WorldContent(
        meta=meta,
        lorebook=lorebook,
        scenes=scenes,
        npcs=npcs,
        presets=presets,
    )


def check_world(root: str | Path) -> list[str]:
    """Return a list of validation problems. Empty list means OK."""
    problems: list[str] = []
    try:
        world = load_world(root)
    except Exception as exc:  # noqa: BLE001 - 诊断工具：任何加载失败都要变成问题条目 # pragma: no cover - diagnostic helper
        return [f"world load failed: {exc}"]

    for entry in world.lorebook:
        subject = (entry.subject or "").strip()
        if subject and subject not in world.npcs:
            problems.append(
                f"lore {entry.id}: 归属角色「{subject}」不在人物表里（这条永远不会被自动注入）"
            )
        # 无关键词只对"纯关键词条目"是问题：常驻与归属条目各自有别的入场通道
        #（2026-09-13 归属通道上线时一并修正——此前会误报常驻/归属条目）。
        if (
            not subject
            and not entry.always_on
            and not any(kw.strip() for kw in entry.keywords)
        ):
            problems.append(
                f"lore {entry.id}: 至少需要一个关键词（无关键词的条目永远不会被命中）"
            )
    # 归属配额：每人 2 条（注入侧硬截断，这里让超额的半成品态被看见）
    by_subject: dict[str, list[str]] = {}
    for entry in world.lorebook:
        subject = (entry.subject or "").strip()
        if subject:
            by_subject.setdefault(subject, []).append(entry.id)
    for subject, ids in by_subject.items():
        if len(ids) > LORE_SUBJECT_CAP:
            problems.append(
                f"lore: 角色「{subject}」有 {len(ids)} 条归属条目，超过上限 "
                f"{LORE_SUBJECT_CAP}（生效只取前 {LORE_SUBJECT_CAP} 条：{'、'.join(ids)}）"
            )
    if world.meta.start_time:
        import datetime as dt

        try:
            dt.datetime.fromisoformat(world.meta.start_time)
        except ValueError:
            problems.append(
                f"start_time should be ISO datetime (e.g. 2026-07-14T08:00:00): {world.meta.start_time}"
            )
    # 主角（2026-09-13）：人物表里有且仅有一张 is_player 卡；没有则报"已补占位卡"。
    players = [card.id for card in world.npcs.values() if card.is_player]
    if len(players) > 1:
        problems.append(f"主角卡只能有一张，当前有 {len(players)} 张：{'、'.join(players)}")
    elif not players:
        problems.append("未声明主角卡（is_player）；载入时已自动补占位卡，请在人物编辑里改名")
    elif players[0] == PLAYER_PLACEHOLDER:
        problems.append("主角卡还是占位名「主角」，请在人物编辑里改为主角真名（日志按此名记录）")
    # 开局场景必须落在注册场景里（空 = 取第一个注册场景，合法）。
    if world.meta.start_scene and not any(s.id == world.meta.start_scene for s in world.scenes):
        problems.append(f"start_scene 不在场景表里：{world.meta.start_scene}")
    return problems


def save_world_assets(root: str | Path, data: dict) -> None:
    """Write world asset files from editor payload.

    data keys: overview, lorebook, scenes, npcs

    强制不变量（2026-09-13）：人物表里必须恰好有一张主角卡——前端不给删除
    按钮，这里是服务端兜底（"主角无法删掉"）。
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    overview = data.get("overview") or {}
    world_info = {
        "id": overview.get("id", root.name),
        "name": overview.get("name", root.name),
        "summary": overview.get("summary", []),
        "opening": overview.get("opening", ""),
        "start_time": overview.get("start_time", ""),
        "start_scene": overview.get("start_scene", ""),
    }
    npcs = data.get("npcs") or {}
    players = [npc_id for npc_id, card in npcs.items() if (card or {}).get("is_player")]
    if len(players) != 1:
        raise ValueError(
            "人物表必须有且只有一张主角卡（is_player）——"
            f"当前 {len(players)} 张：{'、'.join(players) or '无'}"
        )
    write_json_atomic(root / "world.json", world_info)
    write_json_atomic(root / "lorebook.json", data.get("lorebook", []))
    write_json_atomic(root / "scenes.json", data.get("scenes", []))

    npcs_dir = root / "npcs"
    npcs_dir.mkdir(exist_ok=True)
    for old in npcs_dir.glob("*.json"):
        old.unlink()
    for npc_id, card in npcs.items():
        write_json_atomic(npcs_dir / f"{npc_id}.json", card)


if __name__ == "__main__":  # pragma: no cover
    """命令行体检：``python -m app.world.loader <世界目录>``。

    不给参数时不再猜一个世界名（原先写死 ``content/qinghsi``，那是个早已不存在的旧名，
    于是裸跑只会得到一句让人误以为"世界坏了"的 "world load failed"），而是报出用法并
    列出 ``content_root`` 下真实存在的世界——新克隆的仓库里 content/ 是空的，这里能
    直接告诉使用者"空的，先建一个"。``content_root`` 取自 ``Settings``（唯一权威，
    ``CONTENT_ROOT`` 环境变量可覆盖），别再在 CLI 里另写一个路径。
    """
    import sys

    from app.config import get_settings

    if len(sys.argv) < 2:
        content_root = get_settings().content_root
        worlds = (
            sorted(p.name for p in content_root.iterdir() if (p / "world.json").is_file())
            if content_root.is_dir()
            else []
        )
        print(f"用法：python -m app.world.loader {content_root}/<世界名>")
        if worlds:
            print(f"{content_root}/ 下有 {len(worlds)} 个世界：")
            for name in worlds:
                print(f"  - {name}")
        else:
            print(
                f"{content_root}/ 下没有世界（新克隆的仓库这里是空的）。"
                "先在前端「世界工作台 → 新建世界」建一个，或把资产包导入进来。"
            )
        sys.exit(2)

    root = Path(sys.argv[1])
    problems = check_world(root)
    if problems:
        print("校验失败：")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print(f"OK: {root}")