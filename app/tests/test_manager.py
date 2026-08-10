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
