from app.ledger.access import apply_access_override


def test_present_at_derives_from_narrative(session):
    ledger = session.ledger
    ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "net_bar",
        "participants": ["player", "npc_zhuming"],
        "known_by": None,
        "body": "朱明在网吧。",
        "source": "turn",
    }
    ledger.append(ev)
    assert "npc_zhuming" in ledger.present_at("net_bar")
    assert ledger.where_is("npc_zhuming")["location"] == "net_bar"


def test_where_is_and_present(session):
    ledger = session.ledger
    ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "net_bar",
        "participants": ["player", "npc_zhuming"],
        "known_by": None,
        "body": "朱明在网吧。",
        "source": "witness",
    }
    ledger.append(ev)
    assert ledger.where_is("npc_zhuming")["location"] == "net_bar"
    assert "npc_zhuming" in ledger.present_at("net_bar")


def test_visible_to_respects_known_by(session):
    ledger = session.ledger
    public_ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "net_bar",
        "participants": ["player", "npc_zhuming"],
        "known_by": None,
        "body": "朱明在网吧。",
        "source": "turn",
    }
    private_ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "alley_old_building",
        "participants": ["player", "npc_wangrong"],
        "known_by": ["player", "npc_wangrong"],
        "body": "王蓉说了一个秘密。",
        "source": "turn",
    }
    ledger.append(public_ev)
    ledger.append(private_ev)

    visible_to_zhuming = {ev["id"] for ev in ledger.visible_to("npc_zhuming")}
    assert public_ev["id"] in visible_to_zhuming
    assert private_ev["id"] not in visible_to_zhuming

    visible_to_player = {ev["id"] for ev in ledger.visible_to("player")}
    assert private_ev["id"] in visible_to_player


def test_retired_npc_absent_from_present_at(session):
    ledger = session.ledger
    ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "net_bar",
        "participants": ["player", "npc_zhuming"],
        "known_by": None,
        "body": "朱明在网吧。",
        "source": "turn",
    }
    ledger.append(ev)
    assert "npc_zhuming" in ledger.present_at("net_bar")

    # 退场后：位置推导保留（where_is 仍可回溯终点），但不再出现在场名单。
    from app.ledger.save import EntityRuntime

    entity = ledger.save.entities.setdefault("npc_zhuming", EntityRuntime())
    entity.lifecycle = "retired"
    assert ledger.where_is("npc_zhuming")["location"] == "net_bar"
    assert "npc_zhuming" not in ledger.present_at("net_bar")


def test_index_maintained_incrementally(session):
    """Append updates the derived indexes: later location wins for where_is
    and by_participant feeds experiences with visibility filtering."""
    ledger = session.ledger
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T09:00:00",
            "location": "net_bar",
            "participants": ["npc_zhuming"],
            "known_by": None,
            "body": "朱明在网吧。",
            "summary": "朱明在网吧。",
        }
    )
    assert ledger.where_is("npc_zhuming")["location"] == "net_bar"
    assert "npc_zhuming" in ledger.present_at("net_bar")

    # A later, different location overwrites the previous snapshot.
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T10:00:00",
            "location": "school_gate",
            "participants": ["npc_zhuming"],
            "known_by": None,
            "body": "朱明到了校门口。",
            "summary": "朱明到了校门口。",
        }
    )
    assert ledger.where_is("npc_zhuming")["location"] == "school_gate"
    assert "npc_zhuming" not in ledger.present_at("net_bar")
    assert "npc_zhuming" in ledger.present_at("school_gate")

    # A private event involving only 王蓉 stays invisible to 朱明.
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T10:05:00",
            "location": "alley_old_building",
            "participants": ["npc_wangrong"],
            "known_by": ["npc_wangrong"],
            "body": "王蓉一个人来老巷。",
            "summary": "王蓉一个人来老巷。",
        }
    )
    zhuming_mem = "\n".join(ledger.experiences("npc_zhuming", "npc_zhuming"))
    assert "朱明在网吧" in zhuming_mem and "到了校门口" in zhuming_mem
    assert "王蓉一个人来老巷" not in zhuming_mem


def test_cache_stats_counts_hits_and_misses(session):
    ledger = session.ledger
    assert ledger.where_is("npc_zhuming") is None
    stats = ledger.cache_stats_snapshot()
    assert stats["misses"] >= 1 and stats["hits"] == 0
    ledger.append(
        {
            "id": ledger.allocate_event_id(),
            "kind": "narrative",
            "at": "2026-07-14T09:00:00",
            "location": "net_bar",
            "participants": ["npc_zhuming"],
            "known_by": None,
            "body": "朱明在网吧。",
            "summary": "朱明在网吧。",
        }
    )
    assert ledger.where_is("npc_zhuming")["location"] == "net_bar"
    stats = ledger.cache_stats_snapshot()
    assert stats["hits"] >= 1


def test_access_rejudge(session):
    ledger = session.ledger
    ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "school_gate",
        "participants": ["player", "npc_zhuming"],
        "known_by": None,
        "body": "朱明在学校门口说了一件事。",
        "source": "turn",
    }
    ledger.append(ev)

    # 改判私密：名单由玩家/导演直接给定落账（不做检索起草，玩家接受
    # "已传开者收不回"的逻辑代价）——名单外查不到，名单内可引。
    apply_access_override(ledger, ev["id"], ["player", "npc_zhuming"])
    assert ev["id"] not in {item["id"] for item in ledger.visible_to("npc_wangrong")}
    assert ev["id"] in {item["id"] for item in ledger.visible_to("npc_zhuming")}


def test_event_counter_calibrated_from_stream(session):
    """采纳落盘两笔之间崩溃的兜底：重启按流水校准计数器，事件号不复用。"""
    ledger = session.ledger
    ev = {
        "id": "ev_00042",
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "school_gate",
        "participants": ["player", "npc_zhuming"],
        "known_by": None,
        "body": "朱明在学校门口说了一件事。",
        "source": "turn",
    }
    ledger.append(ev)
    # 模拟崩溃窗口：流水已有 42 号，存档计数器还停在旧值。
    ledger.save.meta.next_event_id = 5
    ledger.persist_save()

    from app.ledger.queries import Ledger as LedgerCls

    reloaded = LedgerCls(ledger.world, ledger.save_dir)
    assert reloaded.allocate_event_id() == "ev_00043"


def test_deferred_events_hit_disk_on_flush(session):
    """flush=False 的事件只在内存，flush_events 一次落盘（采纳攒批）。"""
    import json as _json

    ledger = session.ledger
    ev = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "net_bar",
        "participants": ["player"],
        "known_by": None,
        "body": "测试暂存事件。",
        "source": "turn",
    }
    ledger.append(ev, flush=False)
    on_disk_before = ledger.events_path.read_text(encoding="utf-8").strip()
    assert ev["body"] not in on_disk_before

    ledger.flush_events()
    on_disk_after = [
        _json.loads(line)
        for line in ledger.events_path.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert any(item["id"] == ev["id"] for item in on_disk_after)


def test_experiences_recalls_knower_not_participant(session):
    """known_by 名单内但不在 participants 的知情者，记忆切片必须召回该事件。

    场景：玩家与王蓉产生秘密，事后告知朱明 → 原初事件 known_by 扩入朱明。
    visible_to 与 experiences 口径必须一致：谁能引用，谁就能被召回。
    """
    ledger = session.ledger
    secret = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "alley_old_building",
        "participants": ["player", "npc_wangrong"],
        "known_by": ["player", "npc_wangrong"],
        "body": "玩家和王蓉在老巷说了一句悄悄话。",
        "summary": "玩家和王蓉说了一句悄悄话。",
        "source": "turn",
    }
    ledger.append(secret)

    # 落账时朱明就不在名单：不可见也不可召回。
    assert secret["id"] not in {e["id"] for e in ledger.visible_to("npc_zhuming")}
    assert "悄悄话" not in "\n".join(ledger.experiences("npc_zhuming", "npc_zhuming"))

    # 改判扩名单（朱明被告知）：可见性放开，记忆召回必须同步跟上。
    apply_access_override(ledger, secret["id"], ["player", "npc_wangrong", "npc_zhuming"])
    assert secret["id"] in {e["id"] for e in ledger.visible_to("npc_zhuming")}
    assert "悄悄话" in "\n".join(ledger.experiences("npc_zhuming", "npc_zhuming"))

    # 改判收名单（朱明被移出）：召回同步撤销，不残留。
    apply_access_override(ledger, secret["id"], ["player", "npc_wangrong"])
    assert "悄悄话" not in "\n".join(ledger.experiences("npc_zhuming", "npc_zhuming"))


def test_experiences_knower_index_survives_reload(session, tmp_path):
    """by_knower 是派生索引：重建（重开存档）后召回口径不变。"""
    ledger = session.ledger
    secret = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative",
        "at": "2026-07-14T10:00:00",
        "location": "alley_old_building",
        "participants": ["player", "npc_wangrong"],
        "known_by": ["player", "npc_wangrong", "npc_zhuming"],
        "body": "玩家和王蓉在老巷说了一句悄悄话。",
        "summary": "玩家和王蓉说了一句悄悄话。",
        "source": "turn",
    }
    ledger.append(secret)
    from app.ledger.queries import Ledger as LedgerCls

    reloaded = LedgerCls(ledger.world, ledger.save_dir)
    assert "悄悄话" in "\n".join(reloaded.experiences("npc_zhuming", "npc_zhuming"))


def test_known_set_region_boundary(session):
    """已知集听域规则：
    - 卡上有 region 标签 → 按标签听域（出差外地人不知本地旧事）
    - 卡上无标签 → 按亲历事件 region 推导（本地老 NPC 天然正确）
    - 推导为空（刚出场的无历史 NPC）→ 保守：什么区域的公开旧事都不闻
    - 亲历与 known_by 无条件纳入；场景/事件无 region 标签 = 全域公共（单区域包兼容）
    """
    ledger = session.ledger
    for s in ledger.world.scenes:
        if s.id == "main_street":
            s.tags = ["region:a"]
        elif s.id == "net_bar":
            s.tags = ["region:b"]

    ev_a = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T10:00:00",
        "location": "main_street", "participants": ["player"],
        "known_by": None, "body": "主街有集市。", "summary": "主街集市热闹。",
        "source": "turn",
    }
    ev_b = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T11:00:00",
        "location": "net_bar", "participants": ["player"],
        "known_by": None, "body": "网吧举办比赛。", "summary": "网吧举办电竞比赛。",
        "source": "turn",
    }
    ev_priv = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T12:00:00",
        "location": "main_street", "participants": ["player"],
        "known_by": ["player", "npc_zhuming"], "body": "私下给了朱明一样东西。",
        "summary": "玩家私下给朱明东西。",
        "source": "turn",
    }
    for ev in (ev_a, ev_b, ev_priv):
        ledger.append(ev)

    # ① 卡上打标签 region:a 的本地人：同区公开 ✓ + 名单内私密 ✓ + 异区公开 ✗
    ledger.world.npcs["npc_zhuming"].tags = ["region:a"]
    got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
    assert "主街集市" in got
    assert "私下给朱明" in got
    assert "电竞比赛" not in got

    # ② 出差外地人（卡标签 region:b）：名单内私密无条件纳入；a 区旧事不知；b 区新闻照闻
    ledger.world.npcs["npc_zhuming"].tags = ["region:b"]
    got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
    assert "私下给朱明" in got
    assert "主街集市" not in got
    assert "电竞比赛" in got

    # ③ 无历史 NPC（卡无标签、零亲历）：保守——公开旧事一律不闻；known_by 不受限
    ledger.world.npcs["npc_zhuming"].tags = []
    got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
    assert "主街集市" not in got
    assert "电竞比赛" not in got
    assert "私下给朱明" in got

    # ④ 亲历推导：朱明获得一条 a 区亲历事件 → 听域推导出 a → 同区公开回来
    ev_exp = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T13:00:00",
        "location": "main_street", "participants": ["player", "npc_zhuming"],
        "known_by": None, "body": "朱明在主街和玩家碰头。",
        "summary": "朱明与玩家主街碰头。",
        "source": "turn",
    }
    ledger.append(ev_exp)
    got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
    assert "主街集市" in got
    assert "电竞比赛" not in got

    # ⑤ 场景全无标签 = 全域公共：任何人闻到所有公开事件；私密仍按名单
    for s in ledger.world.scenes:
        s.tags = []
    ledger.world.npcs["npc_zhuming"].tags = []
    got = "\n".join(ledger.known_set("npc_wangrong", "main_street"))
    assert "主街集市" in got
    assert "电竞比赛" in got
    assert "私下给朱明" not in got

def test_known_set_adhoc_scene_not_hearsay(session):
    """一次性布景（location 不在场景表）的公开事件 = 哨兵 adhoc：
    不进任何 NPC 的风闻通道（宁可不闻，不错闻）；亲历/known_by 不受影响；
    亲历 ad-hoc 事件也不会把听域推导出 adhoc。"""
    ledger = session.ledger
    for s in ledger.world.scenes:
        if s.id == "main_street":
            s.tags = ["region:a"]
        elif s.id == "net_bar":
            s.tags = ["region:b"]

    ev_adhoc = {
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T10:00:00",
        "location": "dark_alley", "location_name": "无名暗巷",
        "participants": ["player"],
        "known_by": None, "body": "暗巷里当街起了冲突。", "summary": "无名暗巷当街冲突。",
        "source": "turn",
    }
    ledger.append(ev_adhoc)

    # 任何听域都闻不到 ad-hoc 公开事件：a 区本地人、b 区外地人、无标签全域推手皆然
    for tags in (["region:a"], ["region:b"], []):
        ledger.world.npcs["npc_zhuming"].tags = tags
        got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
        assert "暗巷" not in got

    # 亲历通道不受影响：朱明在场亲历 → 进已知集
    ledger.append({
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T11:00:00",
        "location": "dark_alley", "participants": ["player", "npc_zhuming"],
        "known_by": None, "body": "朱明也在暗巷。", "summary": "朱明同在暗巷。",
        "source": "turn",
    })
    ledger.world.npcs["npc_zhuming"].tags = []
    got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
    assert "朱明同在暗巷" in got

    # 亲历 ad-hoc 不把听域推导成 adhoc：另一条 ad-hoc 公开事件仍不风闻
    ledger.append({
        "id": ledger.allocate_event_id(),
        "kind": "narrative", "at": "2026-07-14T12:00:00",
        "location": "rooftop", "location_name": "楼顶天台",
        "participants": ["player"],
        "known_by": None, "body": "天台上有人喊话。", "summary": "楼顶天台有人喊话。",
        "source": "turn",
    })
    got = "\n".join(ledger.known_set("npc_zhuming", "main_street"))
    assert "天台" not in got
