from pathlib import Path

from fastapi.testclient import TestClient

from hfdm.config import AppPaths
from hfdm.database import utc_now
from hfdm.hf_service import HuggingFaceService
from hfdm.main import create_app
from hfdm.schemas import RepoFileInfo, RepoResolution


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


def test_existing_task_can_inspect_repo_and_update_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = AppPaths(
        root=tmp_path,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=tmp_path / "dist",
    )
    app = create_app(paths)
    now = utc_now()
    app.state.db.create_task(
        {
            "id": "editable",
            "repo_id": "owner/repo",
            "requested_revision": "main",
            "commit_hash": "a" * 40,
            "destination": f"models/owner/repo/{'a' * 40}",
            "status": "paused",
            "total_bytes": 10,
            "requires_token": 1,
            "created_at": now,
            "updated_at": now,
        },
        [{"id": "old-file", "task_id": "editable", "path": "old.bin", "size": 10, "status": "paused"}],
    )
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="old.bin", size=10), RepoFileInfo(path="new.bin", size=20)],
        total_bytes=30,
    )
    monkeypatch.setattr(
        HuggingFaceService,
        "resolve_existing",
        lambda self, repo_id, revision, token=None: resolution,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    inspection = client.post("/api/tasks/editable/inspect", json={"hf_token": "hf_secret"})
    configured = client.put(
        "/api/tasks/editable/configuration",
        json={"selected_files": ["new.bin"], "hf_token": "hf_secret"},
    )

    assert inspection.status_code == 200
    assert inspection.json()["selected_files"] == ["old.bin"]
    assert inspection.json()["unavailable_selected_files"] == []
    assert inspection.json()["update_available"] is False
    assert inspection.json()["can_update_in_place"] is True
    assert configured.status_code == 200
    assert configured.json()["created_new"] is False
    assert [file["path"] for file in configured.json()["task"]["files"]] == ["new.bin"]
    assert "hf_secret" not in inspection.text
    assert "hf_secret" not in configured.text


def test_updated_task_api_replaces_predecessor_history(tmp_path: Path, monkeypatch) -> None:
    paths = AppPaths(
        root=tmp_path,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=tmp_path / "dist",
    )
    app = create_app(paths)
    now = utc_now()
    old_commit = "a" * 40
    new_commit = "b" * 40
    app.state.db.create_task(
        {
            "id": "predecessor",
            "repo_id": "owner/repo",
            "requested_revision": "main",
            "commit_hash": old_commit,
            "destination": f"models/owner/repo/{old_commit}",
            "status": "completed",
            "total_bytes": 10,
            "downloaded_bytes": 10,
            "requires_token": 0,
            "created_at": now,
            "updated_at": now,
        },
        [
            {
                "id": "old-file",
                "task_id": "predecessor",
                "path": "old.bin",
                "size": 10,
                "status": "completed",
                "downloaded_bytes": 10,
            }
        ],
    )
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash=new_commit,
        files=[RepoFileInfo(path="new.bin", size=20)],
        total_bytes=20,
    )
    monkeypatch.setattr(
        HuggingFaceService,
        "resolve_existing",
        lambda self, repo_id, revision, token=None: resolution,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    configured = client.put(
        "/api/tasks/predecessor/configuration",
        json={"selected_files": ["new.bin"], "hf_token": None},
    )

    assert configured.status_code == 200
    assert configured.json()["created_new"] is True
    successor_id = configured.json()["task"]["id"]
    assert successor_id != "predecessor"
    assert client.get("/api/tasks/predecessor").status_code == 404
    assert [task["id"] for task in client.get("/api/tasks").json()] == [successor_id]
