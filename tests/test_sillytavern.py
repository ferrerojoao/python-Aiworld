"""SillyTavern 卡导入（``app/world/sillytavern.py`` + 两个端点）的回归测试。

⚠️ 每条断言都对应一个**实测踩到过的坑**（字段名单复数混用、反向布尔、说明页冒充
开场白、把人名当地名、冒号后才是人名、压缩的 iTXt 读不到…）。删掉被测代码里对应的
那一支，这里必须变红——否则它只是"恒真假守卫"，不如删掉。

卡都是**当场构造**的（最小 PNG + tEXt/zTXt/iTXt），不依赖仓库外的真卡文件，CI 也跑得动。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from app.world.draft import check_assets
from app.world.sillytavern import (
    DEFAULT_IMPORT_PLAYER,
    CardParseError,
    build_assets,
    extract,
    greeting_texts,
    inspect,
    is_valid_world_id,
    suggest_world_id,
)
from tests.conftest import GENERIC_LLM_RESPONSE

# ---------------------------------------------------------------------------
# 造卡
# ---------------------------------------------------------------------------


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + b"\x00\x00\x00\x00"


def png_with_chunks(*chunks: bytes) -> bytes:
    """最小 PNG：IHDR + 给定块 + IEND。

    解析器（``_png_text_chunks``）不校验 CRC，所以这里也省了——但必须是真正的
    "长度 + 类型 + 内容"结构，否则测的就不是真解析路径。
    """
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    for chunk in chunks:
        out += chunk
    out += _chunk(b"IEND", b"")
    return bytes(out)


def png_with_text(texts: list[tuple[str, bytes]]) -> bytes:
    return png_with_chunks(
        *[_chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v) for k, v in texts]
    )


def _b64_card(payload: dict, *, spec: str = "chara_card_v3") -> bytes:
    obj = {"spec": spec, "spec_version": "3.0", "data": payload}
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def png_card(payload: dict, *, keyword: str = "ccv3") -> bytes:
    return png_with_text([(keyword, _b64_card(payload))])


def json_card(payload: dict, *, wrap: bool = True) -> bytes:
    obj = (
        {"spec": "chara_card_v3", "spec_version": "3.0", "data": payload} if wrap else payload
    )
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def lore_entry(**kw) -> dict:
    base = {"keys": ["某人"], "content": "某人的设定", "enabled": True, "comment": "某人"}
    base.update(kw)
    return base


def character_card(**kw) -> dict:
    base = {
        "name": "测试卡",
        "description": "一个测试用的人物",
        "character_book": {"entries": []},
    }
    base.update(kw)
    return base


def _assets_for(data: bytes, **kw) -> tuple[dict, dict]:
    """默认**不**把条目转成人物卡。

    `npcs_from_entries=None` 在后端是"全部候选都转"（= 表单默认全勾）。测试要断言
    「世界书里还剩什么」时就必须显式说"一个都不转"——否则条目全变成人物卡、从世界书
    摘走，断言看着在测映射，其实走了另一条路径。
    """
    kw.setdefault("kind", "character")
    kw.setdefault("world_id", "testworld")
    kw.setdefault("npcs_from_entries", [])
    return build_assets(data, **kw)


def _client(tmp_path):
    settings = Settings(
        content_root=tmp_path, data_dir=tmp_path / "data", candidate_ttl_days=7
    )
    return TestClient(create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE})))


# ---------------------------------------------------------------------------
# 取卡：PNG 文本块
# ---------------------------------------------------------------------------


def test_png_先看_ccv3_再看_chara():
    """V3 优先——顺序写反就会读到旧的那份（ST 自己也是 ccv3 优先）。"""
    data = png_with_text(
        [
            ("ccv3", base64.b64encode(json.dumps({"data": {"name": "新卡"}}).encode())),
            ("chara", base64.b64encode(json.dumps({"name": "旧卡"}).encode())),
        ]
    )
    fmt, raw = extract(data)
    assert fmt == "png-ccv3"
    assert raw["data"]["name"] == "新卡"


def test_png_没有_ccv3_时回退_chara():
    data = png_with_text([("chara", base64.b64encode(json.dumps({"name": "旧卡"}).encode()))])
    fmt, raw = extract(data)
    assert fmt == "png-chara"
    assert raw["name"] == "旧卡"


def test_ccv3_是空串时不算数():
    """ST 有时会写一个空块占位；只看"键在不在"会读到空 JSON 而崩。"""
    data = png_with_text(
        [
            ("ccv3", b""),
            ("chara", base64.b64encode(json.dumps({"name": "旧卡"}).encode())),
        ]
    )
    assert extract(data)[0] == "png-chara"


def test_普通_png_给出人话错误():
    with pytest.raises(CardParseError, match="SillyTavern"):
        extract(png_with_text([("Software", b"GIMP")]))


def test_ztxt_文本块也能读():
    # 值同样是 base64 的 JSON（块本身的压缩是 PNG 那一层的，不是卡格式那一层的）
    packed = zlib.compress(base64.b64encode(json.dumps({"name": "压缩卡"}).encode()))
    data = png_with_chunks(_chunk(b"zTXt", b"chara\x00\x00" + packed))
    fmt, raw = extract(data)
    assert fmt == "png-chara"
    assert raw["name"] == "压缩卡"


def test_itxt_压缩与未压缩两种都读():
    """🔴 实测 bug：compression_flag 字节本身可能就是 `\\x00`，按 split 段数定位会漏掉压缩的那份。"""
    for compressed, name in ((False, "明文卡"), (True, "压缩卡")):
        body = base64.b64encode(json.dumps({"name": name}, ensure_ascii=False).encode())
        if compressed:
            body = zlib.compress(body)
        flag = b"\x01" if compressed else b"\x00"
        # keyword \0 flag method language \0 translated \0 text
        blk = _chunk(b"iTXt", b"chara\x00" + flag + b"\x00" + b"en\x00" + b"chara\x00" + body)
        assert extract(png_with_chunks(blk))[1]["name"] == name


def test_一个坏块不会让整张卡失败():
    """解不开的 zTXt 只该跳过那一块——整张卡丢掉才是灾难。"""
    bad = _chunk(b"zTXt", b"chara\x00\x00" + b"not-a-zlib-stream")
    good = _chunk(
        b"tEXt", b"ccv3\x00" + base64.b64encode(json.dumps({"name": "活着"}).encode())
    )
    assert extract(png_with_chunks(bad, good))[1]["name"] == "活着"


# ---------------------------------------------------------------------------
# 取卡：JSON
# ---------------------------------------------------------------------------


def test_v3_卡顶层那份_v1_扁平字段不许盖住_data():
    """实测 V3 卡顶层还留着一份 V1 扁平字段 ⇒ 有 data 就必须只看 data。"""
    raw = {
        "name": "顶层那个旧的",
        "data": {"name": "data里那个新的", "character_book": {"entries": []}},
    }
    data = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    assets, _ = _assets_for(data)
    assert "data里那个新的" in assets["npcs"]
    assert "顶层那个旧的" not in assets["npcs"]


def test_v1_扁平_json_卡也能读():
    assets, _ = _assets_for(json_card({"name": "扁平卡"}, wrap=False))
    assert "扁平卡" in assets["npcs"]


def test_base64_坏了给的是人话():
    with pytest.raises(CardParseError, match="base64"):
        extract(png_with_text([("chara", b"A")]))


def test_文本块里不是_json_给的是人话():
    with pytest.raises(CardParseError, match="JSON"):
        extract(png_with_text([("chara", base64.b64encode(b"hello world"))]))


def test_文本块里的_json_顶层不是对象也被拦():
    """别的软件往 PNG 里写了自己的 tEXt 块（合法 base64、合法 JSON），不能就这么放过去。"""
    with pytest.raises(CardParseError, match="顶层不是对象"):
        extract(png_with_text([("chara", base64.b64encode(b"[1, 2, 3]"))]))


def test_json_顶层不是对象被拦():
    with pytest.raises(CardParseError, match="顶层不是对象"):
        extract(b"[1, 2, 3]")


def test_既不是_png_也不是_utf8_被拦():
    with pytest.raises(CardParseError, match="UTF-8"):
        extract(b"\x89\x00\xff\xfe not utf8")


# ---------------------------------------------------------------------------
# 条目容器与字段名（⚠️ 写错是静默失效）
# ---------------------------------------------------------------------------


def test_卡内是_list_独立世界书是_dict():
    """两种容器形状都得认——卡内是 list、拖进来的独立世界书是 dict（键 = uid）。"""
    from_card = character_card(character_book={"entries": [lore_entry(comment="甲")]})
    assert _assets_for(png_card(from_card))[1]["lore"]["total"] == 1

    worldbook = {"name": "世界书", "entries": {"0": {**lore_entry(comment="乙"), "disable": False}}}
    data = json.dumps(worldbook, ensure_ascii=False).encode("utf-8")
    assert _assets_for(data, kind="worldbook")[1]["lore"]["total"] == 1


def test_entries_写成_list_的独立世界书也能读():
    """独立世界书通例是 dict（键 = uid），但手写或别的工具会给 list。"""
    worldbook = {"name": "世界书", "entries": [lore_entry(comment="甲")]}
    data = json.dumps(worldbook, ensure_ascii=False).encode("utf-8")
    assets, report = _assets_for(data, kind="worldbook")
    assert report["lore"]["total"] == 1
    assert [e["id"] for e in assets["lorebook"]] == ["甲"]


def test_entries_既不是_list_也不是_dict_时当成没有():
    card = character_card(character_book={"entries": "这不是条目容器"})
    assets, report = _assets_for(png_card(card))
    assert report["lore"]["total"] == 0
    assert assets["lorebook"] == []


def test_世界书的_disable_是反向布尔():
    """🔴 独立世界书用 `disable`（**反向**）；当成 `enabled` 读 ⇒ 关着的条目全被导进来。"""
    worldbook = {
        "name": "世界书",
        "entries": {
            "0": {"key": ["开着的"], "content": "开着", "disable": False},
            "1": {"key": ["关着的"], "content": "关着", "disable": True},
        },
    }
    data = json.dumps(worldbook, ensure_ascii=False).encode("utf-8")
    assets, report = _assets_for(data, kind="worldbook")
    assert (report["lore"]["total"], report["lore"]["disabled"]) == (2, 1)
    assert [e["body"] for e in assets["lorebook"]] == ["开着"]


def test_卡内条目的_enabled_是正向():
    card = character_card(
        character_book={
            "entries": [lore_entry(comment="甲"), lore_entry(comment="乙", enabled=False)]
        }
    )
    assets, report = _assets_for(png_card(card))
    assert report["lore"]["disabled"] == 1
    assert [e["id"] for e in assets["lorebook"]] == ["甲"]


def test_同时有_keys_和_key_时以复数_keys_为准():
    """`keys`（卡内）vs `key`（世界书）——混用时不能两个都收，否则触发面翻倍。"""
    card = character_card(character_book={"entries": [lore_entry(keys=["复"], key=["单"])]})
    assets, _ = _assets_for(png_card(card))
    assert assets["lorebook"][0]["keywords"] == ["复"]


def test_key_写成一个字符串也算():
    card = character_card(character_book={"entries": [lore_entry(keys=None, key="孤单的词")]})
    assets, _ = _assets_for(png_card(card))
    assert assets["lorebook"][0]["keywords"] == ["孤单的词"]


def test_关键词去重且去空白():
    card = character_card(character_book={"entries": [lore_entry(keys=[" 甲 ", "甲", "", "乙"])]})
    assets, _ = _assets_for(png_card(card))
    assert assets["lorebook"][0]["keywords"] == ["甲", "乙"]


def test_三道闸门各自计数():
    """关闭 / 空正文 / 既非常驻又无关键词：各自跳过并计数（不砍内容、不静默）。"""
    card = character_card(
        character_book={
            "entries": [
                lore_entry(comment="留"),
                lore_entry(comment="关", enabled=False),
                lore_entry(comment="空", content="   "),
                lore_entry(comment="没词", keys=[], constant=False),
            ]
        }
    )
    assets, report = _assets_for(png_card(card))
    lore = report["lore"]
    assert (lore["total"], lore["disabled"], lore["empty"], lore["keyless"]) == (4, 1, 1, 1)
    assert lore["kept"] == 1
    assert [e["id"] for e in assets["lorebook"]] == ["留"]


def test_常驻条目允许没有关键词():
    """`constant`（常驻）在 AIWorld 里合法地不带关键词；别把它当"没词"扔掉。"""
    card = character_card(
        character_book={"entries": [lore_entry(comment="常驻", keys=[], constant=True)]}
    )
    assets, report = _assets_for(png_card(card))
    assert report["lore"]["keyless"] == 0
    assert assets["lorebook"][0]["always_on"] is True


def test_order_大的排在前面():
    """命中上限是"取列表前 N 条" ⇒ order 大的（更重要的）必须靠前。"""
    card = character_card(
        character_book={
            "entries": [
                lore_entry(comment="小", order=1, keys=["小"]),
                lore_entry(comment="大", order=900, keys=["大"]),
            ]
        }
    )
    assets, _ = _assets_for(png_card(card))
    assert [e["id"] for e in assets["lorebook"]] == ["大", "小"]


def test_条目重名会加后缀():
    card = character_card(character_book={"entries": [lore_entry(order=2), lore_entry(order=1)]})
    assets, _ = _assets_for(png_card(card))
    assert sorted(e["id"] for e in assets["lorebook"]) == ["某人", "某人-2"]


def test_条目没有_comment_时用序号兜底():
    card = character_card(
        character_book={"entries": [lore_entry(comment="", name="", keys=["甲"])]}
    )
    assets, _ = _assets_for(png_card(card))
    assert assets["lorebook"][0]["id"] == "条目1"


# ---------------------------------------------------------------------------
# 候选人物（零模型调用）
# ---------------------------------------------------------------------------


def test_像人物的判据_非常驻且有关键词():
    """实测三张真卡全部命中、零误报；`constant` 的状态栏不会被误当成人。"""
    card = character_card(
        character_book={
            "entries": [
                lore_entry(comment="活泼运动型：夏晴天", keys=["活泼运动", "夏晴天"]),
                lore_entry(comment="绿皮状态栏", constant=True, keys=["状态栏"]),
            ]
        }
    )
    info = inspect(png_card(card))
    assert [c["name"] for c in info["candidates"]] == ["夏晴天"]
    assert info["evidence"]["candidate_npcs"] == 1


def test_人名优先取冒号后面的():
    """🔴 实测真 bug：直接取 keys[0] 会拿到『活泼运动』这种形容。"""
    card = character_card(
        character_book={
            "entries": [lore_entry(comment="活泼运动型：夏晴天", keys=["活泼运动", "夏晴天"])]
        }
    )
    assert inspect(png_card(card))["candidates"][0]["name"] == "夏晴天"


def test_括号里的注释不会污染人名():
    """实测：'大和抚子型：樱井千代 (中文名：千代)' 不剥括号会取到 '千代)'。"""
    card = character_card(
        character_book={
            "entries": [
                lore_entry(comment="大和抚子型：樱井千代 (中文名：千代)", keys=["樱井千代"])
            ]
        }
    )
    assert inspect(png_card(card))["candidates"][0]["name"] == "樱井千代"


def test_没有冒号时回退到第一个关键词():
    card = character_card(
        character_book={"entries": [lore_entry(comment="奥菲莉亚 大王女", keys=["奥菲莉亚"])]}
    )
    assert inspect(png_card(card))["candidates"][0]["name"] == "奥菲莉亚"


def test_候选人物按条目原始顺序排队():
    """世界书本身按 order 重排了，但候选人物列表要跟着**条目原始顺序**（玩家是按顺序填的）。"""
    card = character_card(
        character_book={
            "entries": [
                lore_entry(comment="甲", keys=["甲"], order=900),
                lore_entry(comment="乙", keys=["乙"], order=1),
            ]
        }
    )
    assert [c["name"] for c in inspect(png_card(card))["candidates"]] == ["甲", "乙"]


# ---------------------------------------------------------------------------
# 开局
# ---------------------------------------------------------------------------


def test_元说明页会被识别出来():
    """实测场景卡 first_mes = 「此处仅作为说明，实际开场白请右滑开局」——不是正文。"""
    card = character_card(
        first_mes="此处仅作为说明，实际开场白请右滑开局\n=====================",
        alternate_greetings=["真正的开场白"],
    )
    data = png_card(card)
    assert inspect(data)["evidence"]["first_mes_looks_like_meta"] is True
    assets, report = _assets_for(data, opening_index=1)
    assert assets["overview"]["opening"] == "真正的开场白"
    assert report["opening"]["source"] == "alternate_greetings[0]"


def test_正常开场白不会被误判成说明页():
    card = character_card(first_mes="你推开门，雪落进来。")
    assert inspect(png_card(card))["evidence"]["first_mes_looks_like_meta"] is False


def test_开场白候选按顺序列出():
    card = character_card(first_mes="一", alternate_greetings=["二", "三", ""])
    assert [g["source"] for g in greeting_texts(card)] == [
        "first_mes",
        "alternate_greetings[0]",
        "alternate_greetings[1]",
    ]


def test_opening_index_越界时回退到第一段():
    card = character_card(first_mes="一", alternate_greetings=["二"])
    assets, report = _assets_for(png_card(card), opening_index=99)
    assert assets["overview"]["opening"] == "一"
    assert report["opening"]["source"] == "first_mes"


def test_没有开场白时交给引擎的默认纪实句():
    """卡里没有 first_mes ⇒ 用引擎的默认「<主角>来到<场景>。」（它是位置事实，不是占位符）。"""
    assets, report = _assets_for(png_card(character_card()))
    assert assets["overview"]["opening"] == "旅人来到起点。"
    assert report["opening"]["source"] == "（无）"
    assert report["opening"]["chars"] == 0


def test_宏替换_char_user_和_START():
    card = character_card(first_mes="<START>{{char}} 看了 {{user}} 一眼，{{time}}还留着。")
    assets, report = _assets_for(png_card(card), player_name="旅人")
    opening = assets["overview"]["opening"]
    assert "测试卡" in opening and "旅人" in opening
    assert "<START>" not in opening
    assert "{{time}}" in opening  # 不猜、不删
    assert report["opening"]["macros_left"] == ["{{time}}"]


# ---------------------------------------------------------------------------
# 世界 id
# ---------------------------------------------------------------------------


def test_ascii_卡名转成小写_世界_id():
    assert suggest_world_id("MyCard") == "mycard"
    assert suggest_world_id("兽人 模拟器!!!1.3") == "1-3"


def test_中文卡名退成_st_加八位哈希():
    got = suggest_world_id("高岭爱花")
    assert got.startswith("st-")
    assert len(got) == 11
    assert is_valid_world_id(got)
    assert suggest_world_id("高岭爱花") == got  # 稳定、可复现


def test_非法世界_id_被拦():
    for bad in ("", "有中文", "a" * 65, "a/b"):
        assert not is_valid_world_id(bad)
    with pytest.raises(CardParseError, match="世界 id"):
        _assets_for(png_card(character_card()), world_id="有中文")


def test_卡里没有_name_时被拦():
    with pytest.raises(CardParseError, match="name"):
        _assets_for(png_card({"description": "没名字"}))


def test_未知类型被拦():
    with pytest.raises(CardParseError, match="卡类型"):
        _assets_for(png_card(character_card()), kind="person")


def test_卡名与主角名相同时被拦():
    with pytest.raises(CardParseError, match="主角名"):
        _assets_for(png_card(character_card(name="同名")), player_name="同名")


# ---------------------------------------------------------------------------
# 映射：三类卡 → 三种落点
# ---------------------------------------------------------------------------


def test_人物表恰好一张主角卡():
    """`save_world_assets` 的不变量：不满足就抛 ValueError。"""
    assets, _ = _assets_for(png_card(character_card()))
    players = [n for n in assets["npcs"].values() if n.get("is_player")]
    assert len(players) == 1
    assert players[0]["id"] == DEFAULT_IMPORT_PLAYER


def test_角色卡的卡主成为一张普通_npc_卡():
    card = character_card(name="高岭爱花", description="她是这样一个人", personality="温柔")
    assets, report = _assets_for(png_card(card))
    npc = assets["npcs"]["高岭爱花"]
    assert not npc.get("is_player")
    assert "她是这样一个人" in npc["persona"]
    assert "温柔" in npc["persona"]
    assert report["source_name"] == "高岭爱花"


def test_外貌一律留空不硬猜():
    """ST 的 description 不区分外貌与人格——硬拆就是把人格写进外貌。"""
    assets, _ = _assets_for(png_card(character_card()))
    assert assets["npcs"]["测试卡"]["appearance"] == ""


def test_转成人物卡的条目要从世界书里摘掉():
    """否则同一段设定会被注入两遍（人物卡在场时 + 世界书命中时）。"""
    card = character_card(character_book={"entries": [lore_entry(comment="同学甲", keys=["同学甲"])]})
    data = png_card(card)
    assets, report = _assets_for(data, npcs_from_entries=[{"index": 0, "name": "同学甲"}])
    assert "同学甲" not in [e["id"] for e in assets["lorebook"]]
    assert assets["npcs"]["同学甲"]["persona"] == "某人的设定"
    assert report["npcs"]["turned_from_entries"] == ["同学甲"]
    # 🔴 报告里给玩家看的"世界书几条"必须是**转人后**的数（否则说 1 条、实际 0 条）
    assert report["lore"]["kept"] == 1  # 卡里通过闸门的可用条目
    assert report["lore"]["in_lorebook"] == 0  # 新世界的世界书里一条不剩


def test_表单里改的名字说了算():
    card = character_card(
        character_book={"entries": [lore_entry(comment="活泼运动型：夏晴天", keys=["夏晴天"])]}
    )
    assets, _ = _assets_for(png_card(card), npcs_from_entries=[{"index": 0, "name": "我改的"}])
    assert "我改的" in assets["npcs"]
    assert "夏晴天" not in assets["npcs"]


def test_不勾的条目留在世界书里():
    card = character_card(
        character_book={
            "entries": [lore_entry(comment="甲", keys=["甲"]), lore_entry(comment="乙", keys=["乙"])]
        }
    )
    assets, report = _assets_for(png_card(card), npcs_from_entries=[{"index": 0, "name": "甲"}])
    assert [e["id"] for e in assets["lorebook"]] == ["乙"]
    assert report["lore"]["in_lorebook"] == 1


def test_候选一个都不勾时不转人物():
    card = character_card(character_book={"entries": [lore_entry(comment="甲", keys=["甲"])]})
    assets, report = _assets_for(png_card(card), npcs_from_entries=[])
    assert report["npcs"]["turned_from_entries"] == []
    assert "甲" in [e["id"] for e in assets["lorebook"]]


def test_表单传进来的脏数据被忽略而不是炸掉():
    """表单参数是**外部输入**：不是 dict、或 index 指不到条目，都只该跳过。"""
    card = character_card(character_book={"entries": [lore_entry(comment="甲", keys=["甲"])]})
    assets, report = _assets_for(
        png_card(card), npcs_from_entries=["垃圾", {"index": 999}, {"no_index": 1}]
    )
    assert report["npcs"]["turned_from_entries"] == []
    assert "甲" in [e["id"] for e in assets["lorebook"]]


def test_重名会被跳过并进报告():
    """撞上主角名时不能静默覆盖——那会把主角的卡顶掉。"""
    card = character_card(character_book={"entries": [lore_entry(comment="旅人", keys=["旅人"])]})
    # None = 表单默认全勾（这条路径才会去挑候选、才碰得到撞名）
    assets, report = _assets_for(png_card(card), player_name="旅人", npcs_from_entries=None)
    assert report["npcs"]["skipped_duplicate_names"] == ["旅人"]
    assert assets["npcs"]["旅人"]["is_player"] is True


def test_场景卡的_scenario_和_description_都进_perceivable():
    card = character_card(name="风华学园", scenario="这是一所学校", description="详细环境")
    assets, _ = _assets_for(png_card(card), kind="scene", scene_name="风华学园")
    scene = assets["scenes"][0]
    assert scene["id"] == "风华学园"
    assert "这是一所学校" in scene["perceivable"]
    assert "详细环境" in scene["perceivable"]
    assert assets["overview"]["start_scene"] == "风华学园"


def test_非场景卡的场景名默认是起点():
    assets, _ = _assets_for(png_card(character_card()))
    assert assets["scenes"][0]["id"] == "起点"
    assert assets["scenes"][0]["perceivable"] == ""


def test_场景名留空时场景卡用卡名():
    assets, _ = _assets_for(png_card(character_card(name="风华学园")), kind="scene")
    assert assets["scenes"][0]["id"] == "风华学园"


def test_世界概要按类型取不同字段():
    card = character_card(description="描述", scenario="场景")
    data = png_card(card)
    assert _assets_for(data, kind="worldbook")[0]["overview"]["summary"] == ["描述", "场景"]
    assert _assets_for(data, kind="character")[0]["overview"]["summary"] == ["场景"]
    assert _assets_for(data, kind="scene")[0]["overview"]["summary"] == []


def test_丢弃清单只列卡里真有的字段():
    """不是在报告里全表照抄——只报"这张卡确实带了、但我们没要"的。"""
    assert _assets_for(png_card(character_card()))[1]["dropped_fields"] == []

    card = character_card(creator="某人", tags=["a"], system_prompt="你是测试卡")
    dropped = _assets_for(png_card(card))[1]["dropped_fields"]
    assert "creator" in dropped
    assert "system_prompt" in dropped


def test_正则关键词会被点名():
    """ST 的 use_regex 在 AIWorld 里只做子串包含 ⇒ 含元字符的词必须报出来。"""
    card = character_card(
        character_book={"entries": [lore_entry(comment="甲", keys=[".*危险.*"], use_regex=True)]}
    )
    assets, report = _assets_for(png_card(card))
    assert report["lore"]["use_regex"] == 1
    assert report["lore"]["regex_keys"] == [".*危险.*"]
    assert any("use_regex" in n for n in report["notes"])
    assert assets["lorebook"][0]["keywords"] == [".*危险.*"]  # 原样留着，不猜


def test_正则开关开着但关键词没有元字符时说清楚没差别():
    """别让玩家看到"换成正则处理了"就以为触发面变了——没元字符就是同一个词。"""
    card = character_card(
        character_book={"entries": [lore_entry(comment="甲", keys=["纯文字"], use_regex=True)]}
    )
    _, report = _assets_for(png_card(card))
    assert report["lore"]["use_regex"] == 1
    assert report["lore"]["regex_keys"] == []
    assert any("没有实际差别" in n for n in report["notes"])


def test_次要关键词会被计数并提示():
    card = character_card(character_book={"entries": [lore_entry(comment="甲", secondary_keys=["乙"])]})
    _, report = _assets_for(png_card(card))
    assert report["lore"]["secondary_keys"] == 1
    assert any("次要关键词" in n for n in report["notes"])


def test_常驻条目的代价会算进报告():
    """19k 字的常驻条目每轮都进提示词——报告必须把这笔账说清。"""
    card = character_card(
        character_book={
            "entries": [lore_entry(comment="大常驻", keys=[], constant=True, content="字" * 500)]
        }
    )
    _, report = _assets_for(png_card(card))
    assert report["lore"]["always_on"] == 1
    assert report["lore"]["always_on_chars"] == 500
    assert any("每一轮" in n for n in report["notes"])


# ---------------------------------------------------------------------------
# 映射产物必须过引擎自己的体检
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["character", "scene", "worldbook"])
def test_三种卡的产物都过_check_assets(kind):
    card = character_card(
        name="测试卡",
        scenario="场景",
        first_mes="开场",
        character_book={
            "entries": [
                lore_entry(comment="同学甲", keys=["同学甲"]),
                lore_entry(comment="常驻设定", keys=[], constant=True),
            ]
        },
    )
    assert check_assets(_assets_for(png_card(card), kind=kind)[0]) == []


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


def test_inspect_card_端点纯读(tmp_path):
    with _client(tmp_path) as client:
        data = png_card(character_card(name="高岭爱花"))
        r = client.post(
            "/api/worlds/inspect-card",
            json={"filename": "a.png", "content": base64.b64encode(data).decode()},
        )
        assert r.status_code == 200
        got = r.json()
        assert got["ok"] is True
        assert got["name"] == "高岭爱花"
        assert got["suggested_world_id"]
        # 纯读：一个世界都不该多出来
        assert list(tmp_path.glob("*/world.json")) == []


def test_inspect_card_坏卡是_400_不是_500(tmp_path):
    with _client(tmp_path) as client:
        r = client.post(
            "/api/worlds/inspect-card",
            json={"filename": "x.png", "content": base64.b64encode(b"not a card").decode()},
        )
        assert r.status_code == 400
        assert "JSON" in r.json()["detail"]


def test_inspect_card_空内容是_400(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/api/worlds/inspect-card", json={"filename": "x.png", "content": ""})
        assert r.status_code == 400


def test_import_card_落盘且引擎校验干净(tmp_path):
    with _client(tmp_path) as client:
        data = png_card(character_card(name="高岭爱花", first_mes="开场白"))
        r = client.post(
            "/api/worlds/import-card",
            json={
                "filename": "a.png",
                "content": base64.b64encode(data).decode(),
                "kind": "character",
                "world_id": "gaoling",
                "world_name": "高岭家",
                "player_name": "旅人",
                "scene_name": "",
                "opening_index": 0,
                "npcs_from_entries": None,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["world_id"] == "gaoling"
        assert body["problems"] == []
        assert (tmp_path / "gaoling" / "world.json").exists()

        listed = client.get("/api/worlds").json()["worlds"]
        entry = next(w for w in listed if w["id"] == "gaoling")
        assert entry["ok"] is True and entry["problems"] == []


def test_import_card_世界已存在时改名不覆盖(tmp_path):
    """与 zip 导入同一策略：绝不覆盖玩家已有的世界。"""
    (tmp_path / "gaoling").mkdir(parents=True)
    with _client(tmp_path) as client:
        data = png_card(character_card(name="高岭爱花"))
        r = client.post(
            "/api/worlds/import-card",
            json={
                "filename": "a.png",
                "content": base64.b64encode(data).decode(),
                "kind": "character",
                "world_id": "gaoling",
                "world_name": "",
                "player_name": "",
                "scene_name": "",
                "opening_index": 0,
                "npcs_from_entries": None,
            },
        )
    assert r.status_code == 200
    assert r.json()["world_id"].startswith("gaoling_")
    assert not (tmp_path / "gaoling" / "world.json").exists()


def test_import_card_坏参数是_400(tmp_path):
    with _client(tmp_path) as client:
        data = png_card(character_card())
        r = client.post(
            "/api/worlds/import-card",
            json={
                "filename": "a.png",
                "content": base64.b64encode(data).decode(),
                "kind": "character",
                "world_id": "有中文",
                "world_name": "",
                "player_name": "",
                "scene_name": "",
                "opening_index": 0,
                "npcs_from_entries": None,
            },
        )
        assert r.status_code == 400
