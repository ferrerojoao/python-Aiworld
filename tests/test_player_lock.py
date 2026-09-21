"""主角锁定（2026-09-21）：本局一开演，"谁是主角"就冻结。

用户需求原话："在游戏中途，更换主角容易导致混乱。设计一个方案，让更换主角只能在
没开始游戏（包括重置世界）时进行。一旦事件日志落一条，就不能再改主角。"

**为什么必乱**（换主角 = 把一个人名从所有"以人名为键"的索引里抽走）：事件日志直接
记人名 / ``last_location`` / ``entities[名].states`` / ``by_participant`` 全按名索引，
而目标 ``subject=""`` 是"主角"哨兵、QC 参照区拉的是当前主角的 ``personal_secrets``
→ 换人 = 历史目标静默改归新人 + 泄漏基线整体换人。

**判据**：``Ledger.game_started()`` = 账本里存在 ``source != "opening"`` 的事件。

四条拍板（本轮）：
1. 开场事件**不算**"落一条"（否则重置完立刻又被锁，与"包括重置世界"冲突）
2. 主角**改名一起锁**（日志键就是人名，改名与换人是同一个错位的两种表现）
3. 删掉前端从未调用的死接口 ``GET/PUT /sessions/{sid}/player``
4. 已开局的档丢了主角卡 → **只报不改**（不还原、不猜原主、不落盘）
"""
from __future__ import annotations

import json

from app.runtime.session import create_session


# ---------------------------------------------------------------------------
# 判据本身（Ledger.game_started）
# ---------------------------------------------------------------------------
def _event(event_id: str, **extra) -> dict:
    """最小形状的叙述事件：判据只看 ``source``，其余字段给足以免索引报错。"""
    return {"id": event_id, "kind": "narrative", "participants": [], "location": None, **extra}


def test_game_started_false_on_a_fresh_save(session):
    """开档只写一条开场事件 → 还没开演，主角可换。"""
    assert [e["source"] for e in session.ledger.events] == ["opening"]
    assert session.ledger.game_started() is False


def test_game_started_true_after_one_event_is_written(session):
    session.ledger.append(_event("ev_00002", source="turn"))
    assert session.ledger.game_started() is True


def test_game_started_ignores_an_opening_only_ledger(session):
    """重置 = 清空流水 + 重写一条开场 → 必须回到"未开始"。

    这是"重置世界后能换主角"的**唯一**支撑点：按"账本非空"判会在这里被咬死
    （重置完立刻又锁上，与需求"包括重置世界"直接冲突）。
    """
    session.ledger.append(_event("ev_00002", source="turn"))
    assert session.ledger.game_started() is True
    session.ledger.events = [e for e in session.ledger.events if e["source"] == "opening"]
    assert session.ledger.game_started() is False


def test_game_started_counts_events_without_a_source(session):
    """缺 ``source`` 的旧事件按"已开始"算（fail-closed）：写得下来就说明演过。"""
    session.ledger.append(_event("ev_00002"))
    assert session.ledger.game_started() is True


def test_director_events_also_count(session):
    """导演覆写/记忆注入也算落了一条——改的也是历史，换了主角一样错位。"""
    session.ledger.append(_event("ev_00002", source="director"))
    assert session.ledger.game_started() is True


def test_game_started_false_when_the_world_has_no_opening_prose(world_root):
    """世界没有开场白 → 开档连一条事件都不写 → 更没开演。"""
    world_path = world_root / "world.json"
    raw = json.loads(world_path.read_text(encoding="utf-8"))
    raw["opening"] = ""
    world_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    session = create_session(world_root, "test")
    assert session.ledger.events == []
    assert session.ledger.game_started() is False


# ---------------------------------------------------------------------------
# API：player_locked 与 PUT /world 的 409 闸门
# ---------------------------------------------------------------------------
def _client(tmp_path):
    from tests.test_api import _make_client

    return _make_client(tmp_path)


def _new_session(client) -> str:
    return client.post("/api/sessions", json={"world_id": "qinghsi"}).json()["sid"]


def _payload(client, sid) -> dict:
    """当前资产的可提交形状（= ``PUT /world`` 的 body）。"""
    world = client.get(f"/api/sessions/{sid}/world").json()
    return {
        "overview": world["overview"],
        "lorebook": world["lorebook"],
        "scenes": world["scenes"],
        "npcs": {k: dict(v) for k, v in world["npcs"].items()},
        "axes": world["axes"],
    }


def _locked(client, sid) -> bool:
    return client.get(f"/api/sessions/{sid}/world").json()["player_locked"]


def _start(client, sid) -> None:
    """让账本落一条正文（= 本局开演）。

    ⚠️ 只跑一轮**不够**：一轮只产候选，事件要等「采纳」才写进流水
    （``Transaction.commit`` 是唯一落账点）。
    """
    from tests.test_api import _candidate_id_from_sse

    resp = client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})
    cid = _candidate_id_from_sse(resp.text)
    assert client.post(f"/api/sessions/{sid}/candidates/{cid}/adopt").status_code == 200


def test_world_payload_carries_player_locked(tmp_path):
    """/world 要透出 player_locked —— 前端靠它置灰（判据只在后端一份）。"""
    with _client(tmp_path) as client:
        sid = _new_session(client)
        assert _locked(client, sid) is False
        _start(client, sid)
        assert _locked(client, sid) is True


def test_a_pending_candidate_does_not_lock_the_player(tmp_path):
    """只产了候选、还没采纳 → 账本没动 → 仍算未开始，主角还能换。

    与"一旦事件日志落一条"字面一致：锁定看的是**账本**，不是"有没有在跑"。
    """
    with _client(tmp_path) as client:
        sid = _new_session(client)
        client.post(f"/api/sessions/{sid}/turn", json={"input": "问朱明"})  # 只产候选
        assert _locked(client, sid) is False


def test_swap_player_allowed_before_start(tmp_path):
    """开局前换主角正常放行（"重置世界后换主角"走的就是这条路）。"""
    with _client(tmp_path) as client:
        sid = _new_session(client)
        payload = _payload(client, sid)
        payload["npcs"]["刘星"]["is_player"] = False
        payload["npcs"]["朱明"]["is_player"] = True
        assert client.put(f"/api/sessions/{sid}/world", json=payload).status_code == 200

        world = client.get(f"/api/sessions/{sid}/world").json()["npcs"]
        assert world["朱明"]["is_player"] is True
        assert world["刘星"]["is_player"] is False


def test_swap_player_blocked_after_start(tmp_path):
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        payload = _payload(client, sid)
        payload["npcs"]["刘星"]["is_player"] = False
        payload["npcs"]["朱明"]["is_player"] = True

        resp = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert resp.status_code == 409
        assert "主角已锁定" in resp.json()["detail"]
        assert "刘星" in resp.json()["detail"]  # 说清当前主角是谁
        assert "重置世界" in resp.json()["detail"]  # 并指出唯一出口

        # 闸门在落盘之前：内存与磁盘都没被改动。
        npcs = client.get(f"/api/sessions/{sid}/world").json()["npcs"]
        assert npcs["刘星"]["is_player"] is True
        assert npcs["朱明"]["is_player"] is False


def test_rename_player_blocked_after_start(tmp_path):
    """改名与换人是同一个错位的两种表现（日志键就是人名）→ 一起锁（拍板 2）。"""
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        payload = _payload(client, sid)
        card = payload["npcs"].pop("刘星")
        card["id"] = "林晓"
        payload["npcs"]["林晓"] = card

        resp = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert resp.status_code == 409
        assert "刘星" in resp.json()["detail"]
        assert "刘星" in client.get(f"/api/sessions/{sid}/world").json()["npcs"]


def test_renaming_only_the_inner_id_field_still_blocks(tmp_path):
    """只改卡内 ``id``（不动外层键）也要拦。

    判据取 ``card.id`` 而不是外层 dict 键：``save_world_assets`` 按 dict 键命名文件，
    ``load_world`` 却按 ``card.id`` 建键 → **落盘重载后真正生效的键是 card.id**。
    所以"只改 id 字段"是一次真改名，只看外层键会漏判。前端 ``readNpcs`` 两者一起改，
    所以这条只有手改请求体才碰得到——而那正是要拦的绕行方式。
    """
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        payload = _payload(client, sid)
        payload["npcs"]["刘星"]["id"] = "林晓"  # 只动 id 字段，外层键还是"刘星"

        resp = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert resp.status_code == 409
        assert "主角已锁定" in resp.json()["detail"]


def test_delete_player_blocked_after_start_and_it_is_409_not_400(tmp_path):
    """删主角卡也走同一条收口，且**必须是 409 而不是 400**。

    这条断言钉死的是**顺序**：闸门在 ``check_assets`` 之前。先过不变量校验会抛
    "人物表必须有且只有一张主角卡——当前 0 张"这种技术话，而玩家需要知道的是
    "已开始游戏，主角已锁定"。把它挪到 check_assets 之后，这条会变 400。
    """
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        payload = _payload(client, sid)
        payload["npcs"].pop("刘星")

        resp = client.put(f"/api/sessions/{sid}/world", json=payload)
        assert resp.status_code == 409
        assert "主角已锁定" in resp.json()["detail"]


def test_edit_player_profile_allowed_after_start(tmp_path):
    """锁的边界要窄：增厚已有卡与身份无关 → 外貌 / 人格 / 幕后注 / 自知的隐秘 /
    听域 / has_actor 一律放行（落卡与日常补档全靠这条路）。"""
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        payload = _payload(client, sid)
        card = payload["npcs"]["刘星"]
        card["appearance"] = "黑发"
        card["persona"] = "冷静"
        card["private_note"] = "其实是镇长的私生子"
        card["personal_secrets"] = "欠了赌债"
        card["has_actor"] = True
        card["region"] = ["主街"]

        assert client.put(f"/api/sessions/{sid}/world", json=payload).status_code == 200

        saved = client.get(f"/api/sessions/{sid}/world").json()["npcs"]["刘星"]
        assert saved["private_note"] == "其实是镇长的私生子"
        assert saved["personal_secrets"] == "欠了赌债"
        assert saved["has_actor"] is True
        assert saved["region"] == ["主街"]
        assert saved["is_player"] is True


def test_rename_other_npc_allowed_after_start(tmp_path):
    """非主角卡的改名不受锁影响：锁的是**身份**，不是所有姓名。"""
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        payload = _payload(client, sid)
        # 前端 readNpcs 是"键 = 卡内 id"一起改的（落盘后按 card.id 建键），照它写。
        card = payload["npcs"].pop("朱明")
        card["id"] = "老朱"
        payload["npcs"]["老朱"] = card

        assert client.put(f"/api/sessions/{sid}/world", json=payload).status_code == 200

        npcs = client.get(f"/api/sessions/{sid}/world").json()["npcs"]
        assert "老朱" in npcs
        assert "朱明" not in npcs
        assert npcs["老朱"]["is_player"] is False


def test_reset_unlocks_the_player(tmp_path):
    """重置世界是唯一正当的换主角路径：清空流水 + 重写开场 → 立刻解锁。"""
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)
        assert _locked(client, sid) is True

        assert client.post(f"/api/sessions/{sid}/reset").status_code == 200
        assert _locked(client, sid) is False

        payload = _payload(client, sid)
        payload["npcs"]["刘星"]["is_player"] = False
        payload["npcs"]["朱明"]["is_player"] = True
        assert client.put(f"/api/sessions/{sid}/world", json=payload).status_code == 200
        assert client.get(f"/api/sessions/{sid}/world").json()["npcs"]["朱明"]["is_player"] is True


def test_dead_player_endpoints_are_gone(tmp_path):
    """拍板 3：``GET/PUT /player`` 删掉——少一条能绕过锁的路（前端从未调用过）。

    直接查公开的路由表（openapi）而不是发请求：未知的 ``/api/...`` 会掉进 SPA 静态
    目录兜底（``web/dist`` 那个 mount），在 Windows 上会因 sid 里的 ``:`` 把 os.stat
    抛成 OSError，拿不到干净的 404——"路由还在不在"才是这里真正想断言的东西。
    """
    with _client(tmp_path) as client:
        paths = set(client.get("/openapi.json").json()["paths"])
        assert "/api/sessions/{sid}/player" not in paths
        # 对照组：资产写路径还在（删的是死接口，不是主角这条路）。
        assert "/api/sessions/{sid}/world" in paths


def test_missing_player_card_on_a_started_save_is_only_reported(tmp_path):
    """拍板 4：已开局的档丢了主角卡（外部手改磁盘）→ **只报不改**。

    ``load_world`` 会无条件补一张占位主角卡（引擎处处以"主角名"为键，缺卡会静默
    失配），所以补是必须的——但这里**不还原、不猜原主、不落盘**，只把"你其实已经
    换过一次主角了"这件事说出来，并指明唯一出口是「重置世界」。
    """
    with _client(tmp_path) as client:
        sid = _new_session(client)
        _start(client, sid)

        player_file = tmp_path / "qinghsi" / "npcs" / "刘星.json"
        assert player_file.exists()
        player_file.unlink()

        # 关掉再打开 = 重新走一次 load_world（内存里的旧世界会被换掉）。
        reopened = client.post("/api/sessions/open", json={"world_id": "qinghsi"}).json()["sid"]
        world = client.get(f"/api/sessions/{reopened}/world").json()
        assert world["player_locked"] is True  # 流水还在 → 仍算已开演
        assert world["npcs"]["主角"]["is_player"] is True  # 补的占位卡

        check = client.get(f"/api/sessions/{reopened}/world/check").json()
        assert check["ok"] is False
        assert any("已开始" in p and "主角卡" in p for p in check["problems"]), check["problems"]

        # 只报不改：占位卡没有落盘。
        assert not (tmp_path / "qinghsi" / "npcs" / "主角.json").exists()
