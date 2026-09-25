from __future__ import annotations

from app.runtime.transaction import Candidate, CandidateStore, SideEffects


def _cand(candidate_id: str, turn_id: str, prose: str, created_at: str = "") -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        turn_id=turn_id,
        trace_id="tr",
        mode="initial",
        prose=prose,
        side_effects=SideEffects(),
        created_at=created_at,
    )


def test_reroll_keeps_old_candidates(tmp_path):
    """重抽不清旧稿。这里**故意不给 created_at**——2026-09-25 的 CI 红灯就是这个形状。

    三个键全相等时排序退化成"保住底层遍历次序"，而 `_iter_paths` 走 glob()：
    NTFS 近似字典序（本机绿），ext4 是哈希序（CI 红，读成 [版本三,版本二,版本一]）。
    修法是撞键时用 candidate_id 兜底 ⇒ 这里恢复成本机/CI 一致的确定次序。
    （"与遍历次序无关"这条在本机拿不到牙齿，由下一条 monkeypatch 的用例来咬。）
    """
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_1", "turn_1", "版本一"))
    store.save(_cand("cand_2", "turn_1", "版本二"))
    store.save(_cand("cand_3", "turn_1", "版本三"))

    assert len(store.list_for_turn("turn_1")) == 3
    assert [c.prose for c in store.list_for_turn("turn_1")] == ["版本一", "版本二", "版本三"]


def test_same_second_order_is_stable_and_iteration_independent(tmp_path, monkeypatch):
    """created_at 撞键时次序仍须确定，且与底层遍历次序无关。

    历史存档的时间戳都是秒级的（今天起才写到微秒），秒内连抽两稿一样会撞键；
    撞键时"谁更新"本来就没有事实可用 ⇒ 由 candidate_id 兜底，至少保证稳定。
    把 _iter_paths **倒着**喂进去，就在**任何平台上**都能验这件事——不必等 ext4
    的哈希序来抓我们（只按 glob 次序的实现会在这里直接红）。
    """
    store = CandidateStore(tmp_path)
    for cid in ("cand_1", "cand_2", "cand_3"):
        store.save(_cand(cid, "turn_1", cid, created_at="2026-01-01T00:00:00"))

    paths = list(store._iter_paths())
    assert len(paths) == 3
    monkeypatch.setattr(store, "_iter_paths", lambda: iter(reversed(paths)))

    assert [c.candidate_id for c in store.list_for_turn("turn_1")] == ["cand_1", "cand_2", "cand_3"]


def test_created_at_is_the_primary_sort_key(tmp_path):
    """主键必须是 created_at，不能"干脆按 id 排"。

    次序有语义代价：reroll 取 [-1] 当"最新一稿"。id 是随机 hex，按 id 排等于把
    最新一稿交给运气。这里让 id 的字典序与时间序**相反**（z 最早、a 最晚）：
    只按 id 排的实现会给出 [最晚,中间,最早] ⇒ 直接红。
    """
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_z", "turn_1", "最早", created_at="2026-01-01T00:00:01"))
    store.save(_cand("cand_a", "turn_1", "最晚", created_at="2026-01-01T00:00:03"))
    store.save(_cand("cand_m", "turn_1", "中间", created_at="2026-01-01T00:00:02"))

    assert [c.prose for c in store.list_for_turn("turn_1")] == ["最早", "中间", "最晚"]


def test_adopt_cleanup_deletes_all_turn_candidates(tmp_path):
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_1", "turn_1", "版本一"))
    store.save(_cand("cand_2", "turn_1", "版本二"))
    store.save(_cand("cand_3", "turn_2", "另一回合"))

    store.delete_turn("turn_1")
    assert store.list_for_turn("turn_1") == []
    assert len(store.list_pending()) == 1


def test_discard_removes_whole_turn(tmp_path):
    store = CandidateStore(tmp_path)
    store.save(_cand("cand_1", "turn_1", "版本一"))
    store.save(_cand("cand_2", "turn_1", "版本二"))
    store.delete_turn("turn_1")
    assert store.list_pending() == []
