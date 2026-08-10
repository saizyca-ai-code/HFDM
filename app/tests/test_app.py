from pathlib import Path

from fastapi.testclient import TestClient

from hfdm.config import AppPaths
from hfdm.main import create_app


def test_production_app_serves_api_and_frontend(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    paths = AppPaths(
        root=project_root,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=project_root / "frontend" / "dist",
    )
    with TestClient(create_app(paths)) as client:
        health = client.get("/api/health")
        index = client.get("/")
    assert health.json() == {"status": "ok"}
    assert index.status_code == 200
    assert "<title>HFDM</title>" in index.text
