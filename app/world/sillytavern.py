"""SillyTavern 卡导入（2026-09-25）。

把 ST 的卡翻译成 AIWorld 世界包（``overview/lorebook/scenes/npcs`` 形状），落盘复用
既有的**唯一写路径** ``save_world_assets``——本模块自己**不碰磁盘**，只做解析与映射，
所以可以直接拿真卡跑单元测试。

三种输入形态（靠文件内容嗅探，不看扩展名）：

- **PNG 卡**：tEXt/iTXt 块 ``ccv3``（V3）优先、回退 ``chara``（V2）；值是 base64 的 JSON。
  纯 stdlib 解（``struct`` + ``zlib``），不引 Pillow——Pillow 的 ``.text`` 对 zTXt/iTXt
  的处理随版本变，而 ST 只写 tEXt，自己解没有版本风险。
- **JSON 卡**：``{"spec","spec_version","data"}``（V2/V3）或 V1 扁平 6 字段。
- **JSON 世界书**（俗称"世界卡"）：顶层 ``entries``（dict 键 = uid，或 list）。

🔴 **两类条目的字段名不能混，写错是静默失效**：

=================  =======================  ==========================
                  卡内 ``character_book``   独立世界书
=================  =======================  ==========================
关键词             ``keys``（复数）         ``key``（**单数**）
开关               ``enabled``（正向）      ``disable``（**反向**布尔！）
条目容器           ``entries`` = **list**   ``entries`` = **dict**（键 = uid）
排序               ``insertion_order``      ``order``
=================  =======================  ==========================

🔴 **三类卡（角色/场景/世界）在格式上无法可靠区分**——实测 ``system_prompt`` /
``personality`` / ``scenario`` 在三类卡上都可能全空。所以类型由**玩家在表单里选**，
嗅探只提供证据（``inspect`` 的 evidence）。判错类型的代价是"把人名当地名"，整局正文全错。

🔴 **场景卡/世界卡里的 NPC 本来就是结构化条目**（实测圣花学园 28 条里 26 条是一个学生
一条）。判据 = ``constant == False`` 且 ``keys`` 非空，零模型调用。见 ``candidate_npcs``。

方案与实测数据：``docs/方案-SillyTavern卡导入-AIWorld.md``。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import zlib
from typing import Any

from app.world.draft import DEFAULT_START_SCENE, blank_world_assets

KINDS = ("character", "scene", "worldbook")

# 主角占位名。**不能**用 models.PLAYER_PLACEHOLDER（"主角"）——check_world 会立刻报
# "还是占位名"，导入完就带一条待修，玩家还得先去改一次。
DEFAULT_IMPORT_PLAYER = "旅人"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MACRO_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# 正则元字符：ST 开了 use_regex 时 keys 是正则，AIWorld 只做子串包含，
# 含元字符的 key 必须在报告里点名（不许静默改变语义）。
_REGEX_META_RE = re.compile(r"[\\^$.|?*+()\[\]{}]")
# first_mes 是"元说明页"的特征（实测场景卡：「实际开场白请右滑开局」+ 分隔线）。
_META_GREETING_RE = re.compile(r"右滑|请选择|仅作(为)?说明|以下(是|为)?[^。\n]{0,6}开场白|={5,}")
# 条目 comment 里"名字在冒号后面"的形态（实测场景卡：'活泼运动型：夏晴天'）。
_NAME_AFTER_COLON_RE = re.compile(r"[:：]\s*(?P<name>[^:：]+)$")
# 括号注释（实测 '大和抚子型：樱井千代 (中文名：千代)'——括号里那个中文冒号会把
# "取最后一个冒号后面"的规则带偏，取到 '千代)' ⇒ 必须先剥掉）。
_BRACKET_RE = re.compile(r"[（(【\[][^）)】\]]*[）)】\]]?")

# ST 的卡级字段里，本导入器**刻意不用**的（作废记录见方案 §5）。列出来只为在报告里
# 告诉玩家"这张卡里有这些、我们没要"，不是全表照抄。
_DROPPED_CARD_FIELDS = (
    "system_prompt",
    "post_history_instructions",
    "mes_example",
    "creator_notes",
    "creator",
    "character_version",
    "create_date",
    "tags",
    "talkativeness",
    "fav",
    "avatar",
    "creatorcomment",
    "extensions",
    "group_only_greetings",
)
# 条目级的 ST 专有字段（AIWorld 没有对应物）。
_DROPPED_ENTRY_FIELDS = (
    "secondary_keys",
    "keysecondary",
    "position",
    "depth",
    "role",
    "priority",
    "probability",
    "useProbability",
    "selective",
    "selectiveLogic",
    "group",
    "groupOverride",
    "groupWeight",
    "scanDepth",
    "matchWholeWords",
    "caseSensitive",
    "sticky",
    "cooldown",
    "delay",
    "excludeRecursion",
    "preventRecursion",
    "use_regex",
)


class CardParseError(ValueError):
    """导入失败。message 是给玩家看的人话，端点直接当 400 的 detail。"""


# ---------------------------------------------------------------------------
# 取卡：字节 → (格式标记, 卡 JSON)
# ---------------------------------------------------------------------------


def _png_text_chunks(data: bytes) -> dict[str, str]:
    """读出 PNG 里所有 tEXt / zTXt / iTXt 文本块（坏块跳过，不让整张卡失败）。"""
    out: dict[str, str] = {}
    pos = 8
    while pos + 12 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IEND":
            break
        try:
            if ctype == b"tEXt":
                keyword, _, value = body.partition(b"\x00")
                out[keyword.decode("latin-1")] = value.decode("latin-1")
            elif ctype == b"zTXt":
                keyword, _, rest = body.partition(b"\x00")
                out[keyword.decode("latin-1")] = zlib.decompress(rest[1:]).decode(
                    "utf-8", "replace"
                )
            elif ctype == b"iTXt":
                # 结构：keyword \0 flag(1) method(1) language_tag \0 translated \0 text
                # ⚠️ **不能**用 `body.split(b"\x00", 5)` 定位——flag 字节本身可能就是 `\x00`
                # （未压缩时必然如此），段数会随内容变化：实测 flag=1 时只有 5 段，
                # `len(parts) == 6` 不成立 ⇒ 压缩的 iTXt **静默读不到**。逐字段切。
                keyword, _, rest = body.partition(b"\x00")
                if len(rest) >= 2:
                    compressed = rest[0:1] == b"\x01"
                    rest = rest[2:]  # flag + method
                    _, _, rest = rest.partition(b"\x00")  # language_tag
                    _, _, payload = rest.partition(b"\x00")  # translated_keyword
                    if compressed:
                        payload = zlib.decompress(payload)
                    out[keyword.decode("latin-1")] = payload.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 单个坏块不该让整张卡失败
            continue
    return out


def _b64_json(value: str, *, source: str) -> dict:
    try:
        raw = base64.b64decode(value)
    except Exception as exc:
        raise CardParseError(f"{source}里的 base64 解不开：{exc}") from exc
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise CardParseError(
            f"{source}解出来不是合法 JSON（可能是别的软件写进 PNG 的文本块）：{exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise CardParseError(f"{source}的 JSON 顶层不是对象")
    return obj


def extract(data: bytes) -> tuple[str, dict]:
    """字节 → ``(格式标记, 卡 JSON 原文)``。格式标记形如 ``png-ccv3`` / ``json``。"""
    if data[:8] == _PNG_MAGIC:
        text = _png_text_chunks(data)
        for key in ("ccv3", "chara"):  # V3 优先——ST 自己的读取顺序
            if (text.get(key) or "").strip():
                return f"png-{key}", _b64_json(text[key], source=f"PNG 的 {key} 文本块")
        raise CardParseError(
            "这张 PNG 里没有 chara / ccv3 文本块——它不是 SillyTavern 角色卡"
            "（普通图片没有可导入的资料）。"
        )
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CardParseError(f"这既不是 PNG，也不是 UTF-8 文本文件：{exc}") from exc
    try:
        obj = json.loads(decoded)
    except Exception as exc:
        raise CardParseError(f"JSON 解不开：{exc}") from exc
    if not isinstance(obj, dict):
        raise CardParseError("JSON 顶层不是对象")
    return "json", obj


# ---------------------------------------------------------------------------
# 剥外壳 + 取字段
# ---------------------------------------------------------------------------


def _spec(raw: dict) -> str:
    value = raw.get("spec")
    return value.strip() if isinstance(value, str) else ""


def _payload(raw: dict) -> dict:
    """剥 V2/V3 的 ``data`` 外壳；V1（扁平）原样返回。

    ⚠️ V3 卡的**顶层还留着一份 V1 扁平字段**（实测），所以有 ``data`` 时必须只看 ``data``。
    """
    data = raw.get("data")
    return data if isinstance(data, dict) else raw


def _text(payload: dict, *fields: str) -> str:
    """把若干字段按顺序拼成一段非空文本（空段忽略）。"""
    parts = []
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n\n".join(parts)


def _lore_box(payload: dict) -> Any:
    """条目容器。顶层 ``entries`` ⇒ 独立世界书；否则取卡内 ``character_book``。"""
    if "entries" in payload:
        return payload.get("entries")
    return payload.get("character_book")


def _raw_entries(box: Any) -> list[dict]:
    """条目容器 → list[dict]。兼容 ``character_book``(list) 与独立世界书(dict)。"""
    if isinstance(box, list):
        return [e for e in box if isinstance(e, dict)]
    if not isinstance(box, dict):
        return []
    entries = box.get("entries", box)
    if isinstance(entries, list):
        return [e for e in entries if isinstance(e, dict)]
    if isinstance(entries, dict):
        items = [e for e in entries.values() if isinstance(e, dict)]
        # 独立世界书的 uid 顺序就是编辑顺序，先按 uid 稳定下来，
        # 后面再按 order 稳定排序（同 order 时保持这个顺序）。
        return sorted(items, key=lambda e: _as_int(e.get("uid"), 1 << 30))
    return []


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _entry_keys(entry: dict) -> list[str]:
    """关键词。卡内书本 ``keys``（复数）/ 独立世界书 ``key``（单数）——写错就永不触发。"""
    raw = entry.get("keys")
    if raw is None:
        raw = entry.get("key")
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _entry_enabled(entry: dict) -> bool:
    """卡内书本 ``enabled``（正向）；独立世界书 ``disable``（**反向**）。"""
    if "enabled" in entry:
        return bool(entry.get("enabled"))
    return not bool(entry.get("disable"))


def _entry_order(entry: dict) -> int:
    for field in ("order", "insertion_order"):
        if field in entry:
            return _as_int(entry[field], 100)
    return 100


def _unique_entry_id(entry: dict, index: int, used: set[str]) -> str:
    base = ""
    for field in ("comment", "name"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            base = value.strip()[:40]
            break
    if not base:
        base = f"条目{index + 1}"
    candidate, n = base, 2
    while candidate in used:
        candidate = f"{base}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def _preview(text: str, limit: int = 40) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat[:limit]


def _entry_display_name(entry: dict, keywords: list[str]) -> str:
    """条目 → 人名。

    ST 卡的命名习惯实测三种，都得照顾到：

    - 场景卡：``comment = '活泼运动型：夏晴天'`` ⇒ **名字在冒号后面**。
      ⚠️ 只取 ``keys[0]`` 会拿到"活泼运动"——**这是实测踩到的真 bug**，别退回去。
    - 世界卡：``comment = '奥菲莉亚 大王女'``（名字在前）⇒ 无冒号，退回 ``keys[0]``。
    - 人物卡：``comment = '青叶陆'`` ⇒ 同上。

    这是启发式，会错，所以**导入表单里名字可改**（``npcs_from_entries`` 带 name）。
    """
    comment = ""
    for field in ("comment", "name"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            comment = value.strip()
            break
    # 先剥括号注释：'大和抚子型：樱井千代 (中文名：千代)' 不剥会取到 '千代)'。
    bare = _BRACKET_RE.sub(" ", comment)
    match = _NAME_AFTER_COLON_RE.search(bare)
    if match:
        tail = match.group("name").strip()
        if tail:
            return tail
    if keywords:
        return keywords[0]
    return comment[:40]


# ---------------------------------------------------------------------------
# 世界书条目规范化
# ---------------------------------------------------------------------------


def _normalize_lore(box: Any) -> tuple[list[dict], dict]:
    """→ ``(条目列表(按 order 降序), 计数)``。

    三道闸门（都只"不收录"，不砍内容，各自计数进报告）：

    1. ``enabled == False`` ⇒ 跳过（世界卡实测有 1 条）；
    2. ``content`` 为空 ⇒ 跳过；
    3. **既非常驻、又无关键词** ⇒ 跳过（在 AIWorld 里不可能生效，留着会让 ``check_world``
       报"待修"）。⚠️ 常驻条目**允许**无关键词——引擎侧合法。
    """
    counts: dict[str, Any] = {
        "total": 0,
        "disabled": 0,
        "empty": 0,
        "keyless": 0,
        "secondary_keys": 0,
        "use_regex": 0,
        "regex_keys": [],
    }
    items: list[dict] = []
    used_ids: set[str] = set()
    for index, entry in enumerate(_raw_entries(box)):
        counts["total"] += 1
        if not _entry_enabled(entry):
            counts["disabled"] += 1
            continue
        body = str(entry.get("content") or "").strip()
        if not body:
            counts["empty"] += 1
            continue
        keywords = _entry_keys(entry)
        always_on = bool(entry.get("constant"))
        if not always_on and not keywords:
            counts["keyless"] += 1
            continue
        if entry.get("secondary_keys") or entry.get("keysecondary"):
            counts["secondary_keys"] += 1
        if entry.get("use_regex"):
            counts["use_regex"] += 1
            for key in keywords:
                if _REGEX_META_RE.search(key):
                    counts["regex_keys"].append(key)
        items.append(
            {
                "index": index,
                "id": _unique_entry_id(entry, index, used_ids),
                "name": _entry_display_name(entry, keywords),
                "keywords": keywords,
                "body": body,
                "always_on": always_on,
                "order": _entry_order(entry),
            }
        )
    # order 大的靠近提示词末尾、更有分量；AIWorld 的命中上限是"取列表前 N 条"
    # （hit_entry_ids 按列表顺序截断）⇒ 降序对齐"重要的往上放"。
    items.sort(key=lambda e: -e["order"])
    counts["regex_keys"] = sorted(set(counts["regex_keys"]))
    return items, counts


def candidate_npcs(items: list[dict]) -> list[dict]:
    """像人物的条目：``constant == False`` 且有关键词。

    实测三张真卡全部命中、零误报（反例：世界卡有一条 ``绿皮状态栏`` 是 constant 且有
    关键词，被正确排除）。**不调模型**；名字取 ``_entry_display_name``（启发式，表单里可改）。
    """
    return sorted(
        (
            {
                "index": e["index"],
                "name": e["name"],
                "keywords": e["keywords"],
                "persona": e["body"],
            }
            for e in items
            if not e["always_on"] and e["keywords"]
        ),
        key=lambda e: e["index"],
    )


# ---------------------------------------------------------------------------
# 开局
# ---------------------------------------------------------------------------


def greeting_texts(payload: dict) -> list[dict]:
    """开局候选：``first_mes`` + 各条 ``alternate_greetings``（保序）。"""
    out: list[dict] = []
    first = payload.get("first_mes")
    if isinstance(first, str) and first.strip():
        out.append({"source": "first_mes", "text": first.strip()})
    alts = payload.get("alternate_greetings")
    if isinstance(alts, list):
        for i, alt in enumerate(alts):
            if isinstance(alt, str) and alt.strip():
                out.append({"source": f"alternate_greetings[{i}]", "text": alt.strip()})
    return out


def first_mes_looks_like_meta(payload: dict) -> bool:
    """``first_mes`` 是不是"说明页"。

    实测场景卡的 ``first_mes`` 是「此处仅作为说明，实际开场白请右滑开局」+ 分隔线
    ——**不是正文**，真正的开场白在 ``alternate_greetings`` 里。盲取会拿说明页当开局。
    """
    first = payload.get("first_mes")
    return bool(isinstance(first, str) and _META_GREETING_RE.search(first))


def _substitute_macros(
    text: str, *, char_name: str, player_name: str
) -> tuple[str, list[str]]:
    """``{{char}}``/``{{original}}`` → 卡名；``{{user}}`` → 主角名；``<START>`` 去掉。

    其余宏（``{{time}}``/``{{persona}}``…）**一律留着并进报告**——不猜、不删，
    删了可能吃掉正文里的正常文字。
    """
    unknown: list[str] = []

    def repl(match: re.Match) -> str:
        name = match.group(1).lower()
        if name in ("char", "original"):
            return char_name
        if name == "user":
            return player_name
        unknown.append(match.group(0))
        return match.group(0)

    return _MACRO_RE.sub(repl, text).replace("<START>", "").strip(), unknown


# ---------------------------------------------------------------------------
# 世界 id
# ---------------------------------------------------------------------------


def suggest_world_id(name: str) -> str:
    """卡名 → 合法世界 id（``^[A-Za-z0-9_-]{1,64}$``，见 routes_sessions._WORLD_ID_RE）。

    中文卡名留不下 ASCII ⇒ 退成 ``st-<8 位 sha1>``（稳定、可复现）。
    """
    ascii_part = re.sub(r"[^A-Za-z0-9_-]+", "-", name or "").strip("-")[:40]
    if ascii_part:
        return ascii_part.lower()
    return "st-" + hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:8]


def is_valid_world_id(world_id: str) -> bool:
    """与 ``routes_sessions._WORLD_ID_RE`` 同一判据（世界 id = content/ 下的目录名）。"""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", world_id or ""))


# ---------------------------------------------------------------------------
# 嗅探（纯读，不写盘）
# ---------------------------------------------------------------------------


def inspect(data: bytes) -> dict:
    """看这张卡是什么，**不写任何东西**。给导入表单用。"""
    fmt, raw = extract(data)
    payload = _payload(raw)
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "")
    box = _lore_box(payload)
    items, counts = _normalize_lore(box)
    keeper = [e for e in items if e["always_on"]]
    candidates = candidate_npcs(items)
    entry_fields: set[str] = set()
    for entry in _raw_entries(box):
        entry_fields.update(entry.keys())
    return {
        "format": fmt,
        "spec": _spec(raw) or "v1",
        "name": name,
        "suggested_world_id": suggest_world_id(name),
        # 唯一的"无歧义预选"：顶层有 entries ⇒ 结构上必然是独立世界书。
        # 其余（角色卡 / 场景卡）**不猜**——它们的字段完全一样，判错代价是"把人名当地名"。
        "kind_hint": "worldbook" if "entries" in payload else "",
        "evidence": {
            "description_chars": len(description),
            "has_type_header": bool(re.search(r"(?im)^\s*type\s*[:：]", description)),
            "has_character_book": isinstance(payload.get("character_book"), dict),
            "lore_total": counts["total"],
            "lore_kept": len(items),
            "always_on": len(keeper),
            "always_on_chars": sum(len(e["body"]) for e in keeper),
            "lore_chars": sum(len(e["body"]) for e in items),
            "candidate_npcs": len(candidates),
            "first_mes_looks_like_meta": first_mes_looks_like_meta(payload),
            "use_regex_entries": counts["use_regex"],
            "secondary_keys_entries": counts["secondary_keys"],
            "dropped_entry_fields": sorted(entry_fields & set(_DROPPED_ENTRY_FIELDS)),
        },
        "greetings": [
            {
                "index": i,
                "source": g["source"],
                "chars": len(g["text"]),
                "preview": _preview(g["text"]),
            }
            for i, g in enumerate(greeting_texts(payload))
        ],
        "candidates": [
            {
                "index": c["index"],
                "name": c["name"],
                "keywords": c["keywords"],
                "persona_chars": len(c["persona"]),
                "preview": _preview(c["persona"]),
            }
            for c in candidates
        ],
    }


# ---------------------------------------------------------------------------
# 映射（核心）
# ---------------------------------------------------------------------------


def _pick_npcs(candidates: list[dict], wanted: list[dict] | None) -> list[tuple[int, str, str]]:
    """``[(条目 index, 最终人名, persona)]``。

    ``wanted=None`` ⇒ 全部候选（用算出来的默认名）；给 list 就按它来
    （元素 ``{"index": int, "name": str}``，name 空则用默认名）。
    这一步就是"**资产一律玩家落**"——勾选与改名都发生在玩家手里。
    """
    by_index = {c["index"]: c for c in candidates}
    picked: list[tuple[int, str, str]] = []
    if wanted is None:
        for cand in candidates:
            picked.append((cand["index"], cand["name"], cand["persona"]))
        return picked
    for item in wanted:
        if not isinstance(item, dict):
            continue
        cand = by_index.get(_as_int(item.get("index"), -1))
        if cand is None:
            continue
        name = str(item.get("name") or "").strip() or cand["name"]
        picked.append((cand["index"], name, cand["persona"]))
    return picked


def build_assets(
    data: bytes,
    *,
    kind: str,
    world_id: str,
    world_name: str = "",
    player_name: str = "",
    scene_name: str = "",
    opening_index: int = 0,
    npcs_from_entries: list[dict] | None = None,
) -> tuple[dict, dict]:
    """ST 卡 → 工作台资产形状（``save_world_assets`` 的入参）+ 导入报告。

    不写盘。调用方拿到 assets 后先过 ``check_assets`` 再 ``save_world_assets``——
    保持"写世界只有一条路"。
    """
    if kind not in KINDS:
        raise CardParseError(f"未知的卡类型：{kind}（只能是 {'/'.join(KINDS)}）")
    if not is_valid_world_id(world_id):
        raise CardParseError(
            f"世界 id 只能用 A-Z a-z 0-9 _ -（1–64 位），当前是「{world_id}」。"
        )
    fmt, raw = extract(data)
    payload = _payload(raw)
    name = str(payload.get("name") or "").strip()
    if not name and kind != "worldbook":
        raise CardParseError("卡里没有 name 字段——不知道这个角色/场景叫什么。")

    player = (player_name or "").strip() or DEFAULT_IMPORT_PLAYER
    if kind == "character" and name == player:
        raise CardParseError(f"卡名与主角名都是「{name}」——请改一个主角名。")

    # ---- 场景表。场景名默认：场景卡用卡名（但实测卡名常是**标题**，所以表单可改），
    #      其余用占位名。场景名不是目录名，中文没问题。
    scene_id = (scene_name or "").strip() or (name if kind == "scene" else DEFAULT_START_SCENE)

    # ---- 开局：表单选的那一段（first_mes 可能是说明页，所以不盲取）
    greetings = greeting_texts(payload)
    opening_raw, opening_source = "", "（无）"
    if greetings:
        pick = opening_index if 0 <= opening_index < len(greetings) else 0
        opening_raw = greetings[pick]["text"]
        opening_source = greetings[pick]["source"]
    opening, macros_left = _substitute_macros(
        opening_raw, char_name=name, player_name=player
    )

    scenes = [
        {
            "id": scene_id,
            "aliases": [],
            "perceivable": _text(payload, "scenario", "description")
            if kind == "scene"
            else "",
            "region": "",
        }
    ]

    # ---- 世界概要（每轮给编剧看的背景块）
    if kind == "worldbook":
        summary_parts = [
            v.strip()
            for v in (payload.get("description"), payload.get("scenario"))
            if isinstance(v, str) and v.strip()
        ]
    elif kind == "character":
        scenario = payload.get("scenario")
        summary_parts = (
            [scenario.strip()] if isinstance(scenario, str) and scenario.strip() else []
        )
    else:
        summary_parts = []

    # ---- 人物表
    npcs: dict[str, dict] = {player: {"id": player, "is_player": True}}
    turned: list[str] = []
    skipped_names: list[str] = []
    if kind == "character":
        npcs[name] = {
            "id": name,
            "appearance": "",  # ST 不区分外貌/人格，不硬猜
            "persona": _text(payload, "description", "personality"),
            "has_actor": False,
        }

    # ---- 世界书 + 候选人物
    box = _lore_box(payload)
    items, counts = _normalize_lore(box)
    candidates = candidate_npcs(items)
    turned_indexes: set[int] = set()
    occupied = set(npcs)  # 撞名保护：主角优先，其次先到先得
    for index, npc_name, persona in _pick_npcs(candidates, npcs_from_entries):
        if not npc_name or npc_name in occupied:
            if npc_name:
                skipped_names.append(npc_name)
            continue
        npcs[npc_name] = {
            "id": npc_name,
            "appearance": "",
            "persona": persona,
            "has_actor": False,
        }
        occupied.add(npc_name)
        turned_indexes.add(index)
        turned.append(npc_name)

    lorebook = [
        {
            "id": e["id"],
            "keywords": e["keywords"],
            "body": e["body"],
            "always_on": e["always_on"],
        }
        for e in items
        if e["index"] not in turned_indexes
    ]

    assets = blank_world_assets(
        world_id,
        (world_name or "").strip() or name or world_id,
        player,
        start_scene=scene_id,
        opening=opening,
    )
    assets["overview"]["summary"] = summary_parts
    assets["scenes"] = scenes
    assets["npcs"] = npcs
    assets["lorebook"] = lorebook

    report = {
        "format": fmt,
        "spec": _spec(raw) or "v1",
        "kind": kind,
        "source_name": name,
        "world_id": world_id,
        "player_name": player,
        "scene_name": scene_id,
        "opening": {
            "source": opening_source,
            "chars": len(opening),
            "macros_left": sorted(set(macros_left)),
            "first_mes_looks_like_meta": first_mes_looks_like_meta(payload),
        },
        # ⚠️ 两套数字别混：`kept` / `keyworded` 是**闸门后、转人前**的（"这张卡里有多少条
        # 可用设定"）；`in_lorebook` 才是**新世界的世界书里实际有几条**。转成人物卡的条目
        # 会从世界书里摘掉，两者相差 `turned_into_npcs` 那么多——报告给玩家看的是后者。
        "lore": {
            **counts,
            "kept": len(items),
            "in_lorebook": len(lorebook),
            "always_on": sum(1 for e in items if e["always_on"]),
            "always_on_chars": sum(len(e["body"]) for e in items if e["always_on"]),
            "keyworded": sum(1 for e in items if not e["always_on"]),
            "turned_into_npcs": len(turned),
        },
        "npcs": {"turned_from_entries": turned, "skipped_duplicate_names": skipped_names},
        "dropped_fields": _present_dropped_fields(payload, box),
        "notes": _notes(counts, items, turned, kind),
    }
    return assets, report


def _present_dropped_fields(payload: dict, box: Any) -> list[str]:
    """这张卡**实际存在**的、被丢弃的字段（不是全表照抄）。"""
    found = [
        f
        for f in _DROPPED_CARD_FIELDS
        if isinstance(payload.get(f), (str, list, dict)) and payload.get(f)
    ]
    entry_fields: set[str] = set()
    for entry in _raw_entries(box):
        for field in _DROPPED_ENTRY_FIELDS:
            if entry.get(field):
                entry_fields.add(field)
    return found + sorted(entry_fields)


def _notes(counts: dict, items: list[dict], turned: list[str], kind: str) -> list[str]:
    notes: list[str] = []
    always_on = [e for e in items if e["always_on"]]
    if always_on:
        notes.append(
            f"{len(always_on)} 条常驻条目、共 {sum(len(e['body']) for e in always_on)} 字，"
            "**每一轮都会进提示词**（常驻不受关键词条数上限约束）。"
            "嫌重就去工作台把不要的关掉。"
        )
    if counts.get("secondary_keys"):
        notes.append(
            f"{counts['secondary_keys']} 条条目带 ST 的次要关键词（secondary_keys，"
            "“与”条件）；AIWorld 只有“或” ⇒ 这些条目的触发范围被放宽了。"
        )
    if counts.get("use_regex"):
        if counts["regex_keys"]:
            tail = (
                "；其中含正则元字符、被当普通词处理的关键词："
                + "、".join(counts["regex_keys"][:8])
            )
        else:
            tail = "（这些条目的关键词里没有正则元字符，按普通词处理没有实际差别）"
        notes.append(
            f"{counts['use_regex']} 条条目在 ST 里开了 use_regex（按正则匹配关键词），"
            f"这里一律当普通词（子串包含）{tail}"
        )
    if counts["keyless"]:
        notes.append(
            f"{counts['keyless']} 条既非常驻、又没有关键词，在 AIWorld 里永远不生效，已跳过。"
        )
    if counts["disabled"]:
        notes.append(f"{counts['disabled']} 条在 ST 里是关闭状态，已跳过。")
    if turned:
        notes.append(
            f"{len(turned)} 条条目已转成人物卡，并从世界书里移除（否则同一段设定会注入两遍）。"
            "⚠️ 人物卡的设定只在**该角色在场**时进提示词；想让他在不在场都能被提到，"
            "请在工作台另加一条世界书条目。别名同理：人物卡没有别名机制，"
            "原条目里除了人名以外的关键词不再触发。"
        )
    if kind != "scene":
        notes.append(
            f"开局场景是占位的「{DEFAULT_START_SCENE}」——卡里没有地名信息，"
            "请到工作台改名或补场景。"
        )
    notes.append("主角与人物卡的「外貌」留空（ST 的 description 不区分外貌与人格，不硬猜）。")
    notes.append("世界钟起点用引擎默认（ST 没有时间概念）。")
    return notes
