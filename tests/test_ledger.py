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