from __future__ import annotations

import json

from app.core.store import append_event, read_events, write_json_atomic


def test_append_and_read_events(tmp_path):
    path = tmp_path / "events.jsonl"
    append_event(path, {"id": "ev_1", "body": "第一段"})
    append_event(path, {"id": "ev_2", "body": "第二段"})
    events = read_events(path)
    assert [e["id"] for e in events] == ["ev_1", "ev_2"]


def test_atomic_write(tmp_path):
    path = tmp_path / "save.json"
    write_json_atomic(path, {"a": 1})
    with path.open("r", encoding="utf-8") as f:
        assert json.load(f) == {"a": 1}
    assert not path.with_suffix(".json.tmp").exists()