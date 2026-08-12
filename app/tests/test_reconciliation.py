from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from hfdm.config import AppPaths
from hfdm.database import Database
from hfdm.download_manager import DownloadManager
from hfdm.download_manager import DownloadManagerError
from hfdm.events import EventBroker
from hfdm.main import create_app
from hfdm.reconciliation import DownloadReconciler


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "v1"


def fixture_paths(tmp_path: Path) -> AppPaths:
    data = tmp_path / "data"
    data.mkdir(parents=True)
    database = data / "hfdm.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript((FIXTURE_ROOT / "hfdm-v1.sql").read_text(encoding="utf-8"))
    downloads = tmp_path / "download"
    shutil.copytree(FIXTURE_ROOT / "download", downloads)
    return AppPaths(
        root=tmp_path,
        data=data,
        downloads=downloads,
        database=database,
        frontend_dist=tmp_path / "dist",
    )


def test_reconciliation_separates_transfer_result_from_local_availability(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()

    assert DownloadReconciler(paths, db).run() == 4

    records = {item["id"]: item for item in db.list_tasks()}
    assert records["v1-available"]["status"] == "completed"
    assert records["v1-available"]["local_availability"] == "available"
    assert records["v1-partial"]["local_availability"] == "partial"
    assert records["v1-moved"]["local_availability"] == "moved"
    assert records["v1-changed"]["local_availability"] == "changed"
    assert records["v1-failed"]["local_availability"] == "unknown"
    partial_files = {item["path"]: item["local_status"] for item in records["v1-partial"]["files"]}
    assert partial_files == {"missing.bin": "moved", "present.bin": "available"}


def test_changed_file_is_not_served_as_an_available_download(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    with TestClient(create_app(paths)) as client:
        response = client.get("/api/files/v1-changed/model.bin")
    assert response.status_code == 404


def test_startup_and_manual_reconciliation_are_available_through_api(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    with TestClient(create_app(paths), client=("127.0.0.1", 50000)) as client:
        initial = {item["id"]: item for item in client.get("/api/tasks").json()}
        assert initial["v1-available"]["local_availability"] == "available"

        target = paths.downloads / "models" / "fixture" / "available" / ("a" * 40) / "model.bin"
        target.unlink()
        response = client.post("/api/history/reconcile")
        refreshed = {item["id"]: item for item in client.get("/api/tasks").json()}

    assert response.status_code == 200
    assert response.json()["updated"] == 4
    assert refreshed["v1-available"]["status"] == "completed"
    assert refreshed["v1-available"]["local_availability"] == "moved"


def test_periodic_reconciliation_detects_external_move(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    manager = DownloadManager(paths, db, EventBroker(), reconciliation_interval=0.05)
    target = paths.downloads / "models" / "fixture" / "available" / ("a" * 40) / "model.bin"

    manager.start()
    try:
        assert db.get_task("v1-available")["local_availability"] == "available"  # type: ignore[index]
        target.unlink()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if db.get_task("v1-available")["local_availability"] == "moved":  # type: ignore[index]
                break
            time.sleep(0.05)
        assert db.get_task("v1-available")["local_availability"] == "moved"  # type: ignore[index]
    finally:
        manager.stop()


def test_physical_file_deletion_keeps_history(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    target = paths.downloads / "models" / "fixture" / "available" / ("a" * 40) / "model.bin"
    with TestClient(create_app(paths), client=("127.0.0.1", 50000)) as client:
        response = client.delete("/api/tasks/v1-available/files")
        record = client.get("/api/tasks/v1-available")

    assert response.status_code == 200
    assert response.json()["local_availability"] == "moved"
    assert record.status_code == 200
    assert record.json()["status"] == "completed"
    assert not target.exists()


def test_history_deletion_keeps_physical_files_by_default(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    target = paths.downloads / "models" / "fixture" / "available" / ("a" * 40) / "model.bin"
    with TestClient(create_app(paths), client=("127.0.0.1", 50000)) as client:
        response = client.delete("/api/tasks/v1-available")
        record = client.get("/api/tasks/v1-available")

    assert response.status_code == 204
    assert record.status_code == 404
    assert target.is_file()


def test_moved_download_queues_all_missing_files_as_new_attempt(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    manager = DownloadManager(paths, db, EventBroker())
    manager.reconcile()

    task = manager.redownload_missing("v1-moved")

    assert task["status"] == "queued"
    assert [file["path"] for file in task["files"] if file["status"] == "queued"] == ["model.bin"]
    attempts = db.list_attempts("v1-moved")
    assert [attempt["attempt_number"] for attempt in attempts] == [1, 2]
    assert attempts[0]["status"] == "completed"
    assert attempts[1]["status"] == "queued"


def test_partial_download_only_queues_files_that_were_moved(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    manager = DownloadManager(paths, db, EventBroker())
    manager.reconcile()

    task = manager.redownload_missing("v1-partial")

    files = {file["path"]: file for file in task["files"]}
    assert files["missing.bin"]["status"] == "queued"
    assert files["missing.bin"]["downloaded_bytes"] == 0
    assert files["present.bin"]["status"] == "completed"
    assert files["present.bin"]["downloaded_bytes"] == files["present.bin"]["size"]


def test_available_download_does_not_create_empty_redownload_attempt(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    manager = DownloadManager(paths, db, EventBroker())
    manager.reconcile()

    try:
        manager.redownload_missing("v1-available")
    except DownloadManagerError as exc:
        assert "no moved files" in str(exc)
    else:
        raise AssertionError("available download unexpectedly created a redownload attempt")
    assert len(db.list_attempts("v1-available")) == 1


def test_private_download_requires_token_before_creating_attempt(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    db = Database(paths.database)
    db.initialize()
    manager = DownloadManager(paths, db, EventBroker())
    manager.reconcile()
    with db.connect() as conn:
        conn.execute("UPDATE tasks SET requires_token=1 WHERE id='v1-moved'")
        conn.execute("UPDATE download_records SET requires_token=1 WHERE id='v1-moved'")

    try:
        manager.redownload_missing("v1-moved")
    except DownloadManagerError as exc:
        assert "token is required" in str(exc)
    else:
        raise AssertionError("private download unexpectedly started without a token")
    assert len(db.list_attempts("v1-moved")) == 1


def test_redownload_missing_api_uses_reconciliation_result(tmp_path: Path) -> None:
    paths = fixture_paths(tmp_path)
    app = create_app(paths)
    app.state.manager.reconcile()
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.post("/api/tasks/v1-partial/redownload-missing", json={"hf_token": None})

    assert response.status_code == 200
    files = {file["path"]: file for file in response.json()["files"]}
    assert files["missing.bin"]["status"] == "queued"
    assert files["present.bin"]["status"] == "completed"
