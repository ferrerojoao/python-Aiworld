"""AIWorld 导出包的自描述头与包内清单（2026-10-03）。

诉求（用户原话）：「别的 AI 工具读取了一个生成的文件，立刻就能读到自我声明
这是一个什么东西」。三层声明，缺一层就有一种读者读不到：

1. ``world.json`` / ``save.json`` 各自的 ``_format`` 等字段 —— **单文件级**：
   把 world.json 从包里单独拎出来，它自己也说得清自己是什么。
2. 包内 ``AIWORLD-PACKAGE.json`` —— **机读清单**：包类型、格式版本、生成时刻、
   世界/存档标识，以及**每个条目的用途**。条目由**实际写进 zip 的路径**聚合而来
   （见 :func:`entries`），**不手写** ⇒ 永远不会与包内容漂移。
3. 包内 ``AIWORLD-README.txt`` —— **人读说明**：一段话讲「这是什么、怎么打开、
   每个文件干什么」。由清单渲染（见 :func:`readme_text`），与清单同一真相源。

🔴 本模块是**叶子模块**：只放常量与纯函数，不 import 任何 app 内模块 ——
   ``app/world/models.py`` 与 ``app/ledger/save.py`` 都要引它，反过来引就成环。
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

# ---------------------------------------------------------------------------
# 常量：三种「东西」的格式标识
#
# ``_format`` 回答「这是什么」，``_format_version`` 回答「哪个版本的结构」。
# ⚠️ 版本号只在**声明的形状变了、旧工具会读不懂新包**时才 +1；给下面加一个
# 新字段不算（旧工具忽略不认识的键，正是靠这个才不会一改就失效）。
# ---------------------------------------------------------------------------
FORMAT_WORLD = "aiworld.world"
FORMAT_SAVE = "aiworld.save"
FORMAT_PACKAGE = "aiworld.package"
FORMAT_VERSION = 1
GENERATOR = "AIWorld"

ABOUT_WORLD = "AIWorld 世界资产包：一个世界的人物、场景、世界书与文风设定；不含游戏进度。"
ABOUT_SAVE = "AIWorld 完整存档：一个世界的全部资产，外加进度（时钟、事件历史、角色状态、目标）。"
ABOUT_PACKAGE = "AIWorld 导出包清单：说明这个 zip 是什么、怎么打开、里面每个条目是干什么的。"

# 包内两份自描述文件的名字。带 ``AIWORLD-`` 前缀是有意的：**根层的 README.txt
# 会与世界资产重名**（玩家自己放的说明就没了），且导出侧要靠名字认出"这是上一
# 份包的说明、不该被复制进新包"。
MANIFEST_NAME = "AIWORLD-PACKAGE.json"
README_NAME = "AIWORLD-README.txt"

KIND_WORLD_ASSETS = "world_assets"
KIND_FULL_BACKUP = "full_backup"
KIND_LABELS = {
    KIND_WORLD_ASSETS: "世界资产包（不含进度）",
    KIND_FULL_BACKUP: "完整备份（世界资产 + 存档）",
}

_FILE_ROLES = {
    "world.json": "世界总览：id / 名称 / 简介 / 开场白 / 起始时间 / 起始场景",
    "lorebook.json": "世界书：靠关键词触发（或归属于某个角色）的背景条目",
    "scenes.json": "场景表：每个场景的名称、别名、可见描述、消息域、附图路径",
    "presets.json": "文风预设（只在这个世界覆盖了全局预设时才有）",
    "save.json": "存档运行态：时钟 / 主角所在场景 / 角色状态 / 目标 / 未落卡候选",
    "events.jsonl": "事件流水，每行一条 JSON：这个世界发生过的全部事情，按时间顺序",
}
_DIR_ROLES = {
    "npcs": "人物卡：一人一个 .json，文件名就是人物名（含唯一那张主角卡）",
    "assets": "附图：场景图与人物立绘（png）。引擎不读图，只做界面展示",
}
_UNKNOWN_ROLE = "未识别的条目（原样保留，别丢）"

_WHAT_IT_IS = {
    KIND_WORLD_ASSETS: [
        "这是 AIWorld 导出的一个可玩世界的「设定快照」：人物、场景、世界书、文风设定。",
        "它不含游戏进度——没有存档、没有事件历史。想连进度一起备份，",
        "请用 AIWorld 里的「导出完整备份」。",
    ],
    KIND_FULL_BACKUP: [
        "这是 AIWorld 导出的一个世界的「完整备份」：全部世界资产，外加游戏进度",
        "（时钟、事件日志、角色状态、目标）。导入它就能从备份时的状态接着玩。",
    ],
}


def now_iso() -> str:
    """本机时区的当前时刻（秒级，带偏移），如 ``2026-10-03T15:12:34+08:00``。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def is_package_metadata(name: str) -> bool:
    """这个包内路径是不是**包级说明**（清单 / 人读说明）而非世界资产？

    两侧都要认它，各有一半理由：

    - **导入侧必须跳过**：说明是「那一个 zip 的」，不是世界的内容。落进世界目录
      后它会跟着下一次导出被打包，于是新包里出现第二份同名说明（正文还是旧的）。
    - **导出侧必须跳过**：老世界目录里可能残留一份（早期导入留下的），复制进来
      会与本次新写的那份同名重复。
    """
    return PurePosixPath(name).name in (MANIFEST_NAME, README_NAME)


def entries(paths: Iterable[str]) -> list[dict[str, Any]]:
    """包内路径 → 条目清单：顶层文件各一条，目录**合并**成一条并计数。

    只聚到第一层：一个世界可能有几十个 ``npcs/*.json``，逐条列出来会让清单比正文
    还长，而读者要的只是「里面有哪几类东西」；``count`` 让它仍能回答「是不是少了」。
    """
    out: list[dict[str, Any]] = []
    dirs: dict[str, int] = {}
    for rel in sorted({p for p in paths if p}):
        if "/" in rel:
            head = rel.split("/", 1)[0] + "/"
            dirs[head] = dirs.get(head, 0) + 1
            continue
        out.append({"path": rel, "what": _FILE_ROLES.get(rel, _UNKNOWN_ROLE)})
    for head in sorted(dirs):
        out.append(
            {
                "path": head,
                "what": _DIR_ROLES.get(head.rstrip("/"), _UNKNOWN_ROLE),
                "count": dirs[head],
            }
        )
    return out


def build_manifest(
    *,
    kind: str,
    files: Iterable[str],
    world_id: str,
    world_name: str,
    save: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """包清单（机读）。``entries`` 由 ``files`` 现算 ⇒ 清单不手写、不会漂。"""
    manifest: dict[str, Any] = {
        "_format": FORMAT_PACKAGE,
        "_format_version": FORMAT_VERSION,
        "_generator": GENERATOR,
        "_about": ABOUT_PACKAGE,
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "created_at": now_iso(),
        "world": {"id": world_id, "name": world_name},
        "entries": entries(files),
    }
    if save:
        manifest["save"] = save
    return manifest


def readme_text(manifest: dict[str, Any]) -> str:
    """人读说明。**由清单渲染**——两者同一真相源，不会各说各话。"""
    kind = str(manifest.get("kind") or "")
    world = manifest.get("world") or {}
    save = manifest.get("save") or {}
    title = KIND_LABELS.get(kind, "AIWorld 导出包")
    lines: list[str] = [f"AIWorld {title}", "=" * 40, "", "这是什么"]
    lines += [f"  {para}" for para in _WHAT_IT_IS.get(kind, ["（未知包类型，请用 AIWorld 打开）"])]
    lines += [
        "",
        "怎么打开",
        "  推荐：在 AIWorld 的「世界工作台 → 导入世界」里选这个 zip 文件。",
        "  也可以直接解压：里面都是普通的 JSON / PNG 文件，用任何文本编辑器都能看。",
        "",
        "这个包里有什么",
        f"  {MANIFEST_NAME}    机器可读的清单（AI 工具读这个；下面这份说明也由它生成）",
        f"  {README_NAME}    你正在读的这一页",
    ]
    for item in manifest.get("entries") or []:
        count = item.get("count")
        suffix = f"（{count} 个文件）" if count else ""
        lines.append(f"  {item.get('path', '')}{suffix}    {item.get('what', '')}")
    lines += [
        "",
        "格式标识",
        f'  world.json 里的 "_format" = "{FORMAT_WORLD}"。',
        f'  {MANIFEST_NAME} 里的 "_format" = "{FORMAT_PACKAGE}"，'
        f'"_format_version" = {manifest.get("_format_version", FORMAT_VERSION)}。',
        "  格式版本只在「包的结构变了、旧工具会读不懂」时才增加。",
        "",
        "导出信息",
        f"  包类型：{manifest.get('kind_label', kind)}",
        f"  导出时间：{manifest.get('created_at', '')}",
        f"  世界：{world.get('name', '')}（id: {world.get('id', '')}）",
    ]
    if save:
        lines.append(
            f"  存档：{save.get('name', '')}（时钟 {save.get('clock', '')}，共 {save.get('events', 0)} 条事件）"
        )
    return "\n".join(lines) + "\n"


def write_package_files(zf: zipfile.ZipFile, manifest: dict[str, Any]) -> None:
    """把清单与人读说明写进 zip。**先写**它们，好让它们排在包的最前面。

    ⚠️ 调用方须先按 :func:`is_package_metadata` 过滤要复制的文件，否则同名重复。
    """
    zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    # README 带 UTF-8 BOM：它的读者默认是 Windows 记事本，老版本按 ANSI 解会乱码。
    zf.writestr(README_NAME, readme_text(manifest).encode("utf-8-sig"))
