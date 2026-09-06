from app.ledger.access import apply_access_override, rejudge_private


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

    viewers = rejudge_private(ledger, ev["id"])
    assert "player" in viewers
    assert "npc_zhuming" in viewers

    apply_access_override(ledger, ev["id"], viewers)
    assert ev["id"] not in {item["id"] for item in ledger.visible_to("npc_wangrong")}