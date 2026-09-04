import shutil

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import FakeLLM
from app.main import create_app
from tests.conftest import GENERIC_LLM_RESPONSE, WORLD_ROOT


def test_root_and_world_listing(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app) as client:
        assert "AIWorld" in client.get("/").text
        worlds = client.get("/api/worlds").json()["worlds"]
        assert worlds and worlds[0]["id"] == "qinghsi"


def test_restores_sessions_after_restart(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app) as client:
        client.post("/api/sessions", json={"world_id": "qinghsi", "save_name": "main"})

    app2 = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app2) as client2:
        sessions = client2.get("/api/sessions").json()["sessions"]
        assert any(s["save_name"] == "main" for s in sessions)


def test_open_existing_session(tmp_path):
    world_root = tmp_path / "qinghsi"
    shutil.copytree(WORLD_ROOT, world_root, ignore=shutil.ignore_patterns("saves"))
    settings = Settings(content_root=tmp_path, candidate_ttl_days=7)
    app = create_app(settings=settings, llm=FakeLLM({"*": GENERIC_LLM_RESPONSE}))
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()
        opened = client.post(
            "/api/sessions/open", json={"world_id": "qinghsi", "save_name": "main"}
        ).json()
        assert opened["sid"] == created["sid"]