from pathlib import Path

import pytest

from hfdm.config import AppPaths
from hfdm.database import Database
from hfdm.download_manager import DownloadManager, DownloadManagerError
from hfdm.events import EventBroker
from hfdm.schemas import RepoFileInfo, RepoResolution


def make_manager(tmp_path: Path) -> DownloadManager:
    paths = AppPaths(
        root=tmp_path,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=tmp_path / "dist",
    )
    paths.ensure()
    db = Database(paths.database)
    db.initialize()
    return DownloadManager(paths, db, EventBroker())


def test_task_can_pause_and_resume_without_starting_worker(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="README.md", size=10)],
        total_bytes=10,
    )
    task = manager.create_task(resolution, ["README.md"], "hf_test")
    assert task["destination"] == f"models/owner/repo/{'a' * 40}"
    assert manager.task_destination(task) == (
        tmp_path / "download" / "models" / "owner" / "repo" / ("a" * 40)
    ).resolve()
    assert manager.pause(task["id"])["status"] == "paused"
    assert manager.resume(task["id"])["status"] == "queued"


@pytest.mark.parametrize("path", ["../secret", "CON", "bad:name.bin", "/absolute"])
def test_unsafe_repo_paths_are_rejected(tmp_path: Path, path: str) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path=path, size=10)],
        total_bytes=10,
    )
    with pytest.raises(DownloadManagerError):
        manager.create_task(resolution, [path], None)


def test_existing_absolute_destination_is_migrated(tmp_path: Path) -> None:
    paths = AppPaths(
        root=tmp_path,
        data=tmp_path / "data",
        downloads=tmp_path / "download",
        database=tmp_path / "data" / "hfdm.sqlite3",
        frontend_dist=tmp_path / "dist",
    )
    paths.ensure()
    db = Database(paths.database)
    db.initialize()
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="README.md", size=10)],
        total_bytes=10,
    )
    manager = DownloadManager(paths, db, EventBroker())
    task = manager.create_task(resolution, ["README.md"], None)
    db.set_task_destination(task["id"], r"I:\old-location\download\models\owner\repo\commit")

    DownloadManager(paths, db, EventBroker())

    migrated = db.get_task(task["id"])
    assert migrated is not None
    assert migrated["destination"] == f"models/owner/repo/{'a' * 40}"


def test_queued_task_selection_can_be_edited_in_place(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    initial = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[
            RepoFileInfo(path="a.bin", size=10),
            RepoFileInfo(path="b.bin", size=20),
        ],
        total_bytes=30,
    )
    task = manager.create_task(initial, ["a.bin", "b.bin"], None)
    old_ids = {file["path"]: file["id"] for file in task["files"]}
    refreshed = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[
            RepoFileInfo(path="b.bin", size=20),
            RepoFileInfo(path="c.bin", size=30),
        ],
        total_bytes=50,
    )

    configured, created_new = manager.reconfigure_task(
        task["id"], refreshed, ["b.bin", "c.bin"], "hf_memory_only"
    )

    assert created_new is False
    assert configured["id"] == task["id"]
    assert configured["total_bytes"] == 50
    files = {file["path"]: file for file in configured["files"]}
    assert set(files) == {"b.bin", "c.bin"}
    assert files["b.bin"]["id"] == old_ids["b.bin"]
    assert files["c.bin"]["id"] != old_ids["a.bin"]
    database_files = list(manager.paths.data.glob("hfdm.sqlite3*"))
    assert all(b"hf_memory_only" not in path.read_bytes() for path in database_files)


def test_updated_commit_creates_successor_and_removes_pending_task(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    initial = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="a.bin", size=10)],
        total_bytes=10,
    )
    original = manager.create_task(initial, ["a.bin"], None)
    updated = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="b" * 40,
        files=[RepoFileInfo(path="a.bin", size=11), RepoFileInfo(path="new.bin", size=5)],
        total_bytes=16,
    )

    successor, created_new = manager.reconfigure_task(
        original["id"], updated, ["a.bin", "new.bin"], None
    )

    assert created_new is True
    assert successor["id"] != original["id"]
    assert successor["commit_hash"] == "b" * 40
    assert manager.db.get_task(original["id"]) is None


def test_terminal_task_configuration_replaces_history_but_keeps_files(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="a.bin", size=10)],
        total_bytes=10,
    )
    original = manager.create_task(resolution, ["a.bin"], None)
    destination = manager.task_destination(original)
    destination.mkdir(parents=True)
    target = destination / "a.bin"
    target.write_bytes(b"0123456789")
    manager.db.update_file(original["files"][0]["id"], status="completed", downloaded_bytes=10)
    manager.db.update_task(original["id"], status="completed", downloaded_bytes=10)

    successor, created_new = manager.reconfigure_task(original["id"], resolution, ["a.bin"], None)

    assert created_new is True
    assert successor["id"] != original["id"]
    assert manager.db.get_task(original["id"]) is None
    assert target.read_bytes() == b"0123456789"
    assert [task["id"] for task in manager.db.list_tasks()] == [successor["id"]]


def test_failed_successor_creation_keeps_original_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="a.bin", size=10)],
        total_bytes=10,
    )
    original = manager.create_task(resolution, ["a.bin"], None)
    manager.db.update_task(original["id"], status="completed")

    def fail_create(*args, **kwargs):
        raise DownloadManagerError("successor creation failed")

    monkeypatch.setattr(manager, "create_task", fail_create)
    with pytest.raises(DownloadManagerError, match="successor creation failed"):
        manager.reconfigure_task(original["id"], resolution, ["a.bin"], None)

    assert manager.db.get_task(original["id"])["status"] == "completed"  # type: ignore[index]


def test_active_task_must_be_paused_before_configuration_change(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        repo_id="owner/repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="a.bin", size=10)],
        total_bytes=10,
    )
    task = manager.create_task(resolution, ["a.bin"], None)
    manager.db.update_task(task["id"], status="downloading")

    with pytest.raises(DownloadManagerError, match="Pause the active task"):
        manager.reconfigure_task(task["id"], resolution, ["a.bin"], None)


def test_auth_required_task_returns_to_queue_when_configuration_supplies_token(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        repo_id="owner/private-repo",
        requested_revision="main",
        commit_hash="a" * 40,
        files=[RepoFileInfo(path="a.bin", size=10)],
        total_bytes=10,
    )
    task = manager.create_task(resolution, ["a.bin"], "hf_initial")
    manager.db.bulk_file_status(task["id"], ["queued"], "paused")
    manager.db.update_task(task["id"], status="auth_required")

    configured, created_new = manager.reconfigure_task(
        task["id"], resolution, ["a.bin"], "hf_replacement"
    )

    assert created_new is False
    assert configured["status"] == "queued"
    assert configured["files"][0]["status"] == "queued"
    database_files = list(manager.paths.data.glob("hfdm.sqlite3*"))
    assert all(b"hf_replacement" not in path.read_bytes() for path in database_files)
