import pytest
from fastapi.testclient import TestClient

import ui.backend.main as backend_main
from ui.backend.main import app


client = TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/api/demo-cases",
        "/api/demo-cases/skill_draft_preview",
        "/api/demo-cases/failure_repair/trace",
        "/api/not-a-real-route",
    ],
)
def test_retired_and_unknown_api_routes_are_not_masked_by_spa(path, tmp_path, monkeypatch):
    index_file = tmp_path / "index.html"
    index_file.write_text("<html><body>built spa</body></html>", encoding="utf-8")
    monkeypatch.setattr(backend_main, "FRONTEND_DIR", tmp_path)

    response = client.get(path)

    assert response.status_code == 404
    assert "built spa" not in response.text


def test_frontend_deep_link_serves_spa_index_when_build_exists(tmp_path, monkeypatch):
    index_file = tmp_path / "index.html"
    index_file.write_text("<html><body>built spa</body></html>", encoding="utf-8")
    monkeypatch.setattr(backend_main, "FRONTEND_DIR", tmp_path)

    response = client.get("/settings")

    assert response.status_code == 200
    assert "built spa" in response.text
