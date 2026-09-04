from app.world.loader import check_world, load_world


def test_load_qinghsi(world_root):
    world = load_world(world_root)
    assert world.meta.id == "qinghsi"
    assert len(world.scenes) >= 3
    assert "npc_zhuming" in world.npcs
    assert world.npcs["npc_zhuming"].has_actor is True


def test_check_world_ok(world_root):
    assert check_world(world_root) == []