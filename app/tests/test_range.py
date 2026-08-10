from pathlib import Path

from fastapi.testclient import TestClient

from hfdm.config import AppPaths
from hfdm.database import utc_now
from hfdm.main import create_app


def test_range_download(tmp_path: Path) -> None:
    paths = AppPaths(
        root=tmp_path,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=tmp_path / "frontend" / "dist",
    )
    app = create_app(paths)
    destination = paths.downloads / "models" / "owner" / "repo" / ("a" * 40)
    destination.mkdir(parents=True)
    (destination / "hello.bin").write_bytes(b"0123456789")
    now = utc_now()
    app.state.db.create_task(
        {
            "id": "task",
            "repo_id": "owner/repo",
            "requested_revision": "main",
            "commit_hash": "a" * 40,
            "destination": f"models/owner/repo/{'a' * 40}",
            "status": "completed",
            "total_bytes": 10,
            "requires_token": 0,
            "created_at": now,
            "updated_at": now,
        },
        [{"id": "file", "task_id": "task", "path": "hello.bin", "size": 10, "status": "completed"}],
    )
    with TestClient(app) as client:
        response = client.get("/api/files/task/hello.bin", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
