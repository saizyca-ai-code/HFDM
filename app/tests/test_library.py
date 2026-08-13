from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hfdm.config import AppPaths
from hfdm.database import Database, utc_now
from hfdm.main import create_app
from hfdm.reconciliation import DownloadReconciler


def make_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(
        root=tmp_path,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=tmp_path / "dist",
    )
    paths.ensure()
    return paths


def add_completed_record(
    db: Database,
    *,
    record_id: str,
    commit_hash: str,
    files: list[tuple[str, int]],
) -> None:
    now = utc_now()
    db.create_task(
        {
            "id": record_id,
            "repo_id": "owner/repo",
            "requested_revision": "main",
            "commit_hash": commit_hash,
            "destination": f"models/owner/repo/{commit_hash}",
            "status": "completed",
            "total_bytes": sum(size for _, size in files),
            "requires_token": 0,
            "created_at": now,
            "updated_at": now,
        },
        [
            {
                "id": f"{record_id}:{index}",
                "task_id": record_id,
                "path": path,
                "size": size,
                "status": "completed",
            }
            for index, (path, size) in enumerate(files)
        ],
    )
    for file in db.get_task(record_id)["files"]:  # type: ignore[index]
        db.update_file(file["id"], status="completed", downloaded_bytes=file["size"])
    db.update_task(record_id, status="completed", downloaded_bytes=sum(size for _, size in files))


def test_library_groups_duplicate_records_and_unions_completed_files(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    commit = "a" * 40
    destination = paths.downloads / "models" / "owner" / "repo" / commit
    destination.mkdir(parents=True)
    (destination / "model.bin").write_bytes(b"model")
    (destination / "config.json").write_bytes(b"{}")
    add_completed_record(db, record_id="first", commit_hash=commit, files=[("model.bin", 5)])
    add_completed_record(
        db,
        record_id="second",
        commit_hash=commit,
        files=[("model.bin", 5), ("config.json", 2)],
    )
    DownloadReconciler(paths, db).run()

    library = db.list_library_items()

    assert len(db.list_tasks()) == 2
    assert len(library) == 1
    assert library[0]["history_count"] == 2
    assert library[0]["local_availability"] == "available"
    assert [file["path"] for file in library[0]["files"]] == ["config.json", "model.bin"]
    assert {file["record_id"] for file in library[0]["files"]}.issubset({"first", "second"})


def test_library_keeps_different_commits_as_separate_items(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    for record_id, commit in (("first", "a" * 40), ("second", "b" * 40)):
        destination = paths.downloads / "models" / "owner" / "repo" / commit
        destination.mkdir(parents=True)
        (destination / "model.bin").write_bytes(b"model")
        add_completed_record(db, record_id=record_id, commit_hash=commit, files=[("model.bin", 5)])
    DownloadReconciler(paths, db).run()

    assert len(db.list_library_items()) == 2


def test_library_api_returns_aggregated_items(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    commit = "a" * 40
    destination = paths.downloads / "models" / "owner" / "repo" / commit
    destination.mkdir(parents=True)
    (destination / "model.bin").write_bytes(b"model")
    add_completed_record(app.state.db, record_id="first", commit_hash=commit, files=[("model.bin", 5)])
    add_completed_record(app.state.db, record_id="second", commit_hash=commit, files=[("model.bin", 5)])
    app.state.manager.reconcile()

    with TestClient(app) as client:
        response = client.get("/api/library")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["history_count"] == 2


def test_open_library_folder_requires_local_admin(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app.state.manager,
        "open_task_folder",
        lambda record_id, scope: calls.append((record_id, scope)),
    )

    local = TestClient(app, client=("127.0.0.1", 50000))
    visitor = TestClient(app, client=("192.168.1.20", 50000))
    opened = local.post("/api/library/record-1/open-folder?scope=source")
    forbidden = visitor.post("/api/library/record-1/open-folder?scope=source")

    assert opened.status_code == 200
    assert opened.json() == {"opened": True}
    assert forbidden.status_code == 403
    assert calls == [("record-1", "source")]


def test_duplicate_moved_file_only_restores_through_one_record(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    commit = "a" * 40
    add_completed_record(db, record_id="first", commit_hash=commit, files=[("model.bin", 5)])
    add_completed_record(db, record_id="second", commit_hash=commit, files=[("model.bin", 5)])
    DownloadReconciler(paths, db).run()

    item = db.list_library_items()[0]

    assert item["local_availability"] == "moved"
    assert len(item["restore_record_ids"]) == 1
