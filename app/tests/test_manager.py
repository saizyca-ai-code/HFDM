import io
from pathlib import Path

import pytest

from hfdm.config import AppPaths
from hfdm.database import Database
from hfdm.download_manager import ActiveWorker, DownloadManager, DownloadManagerError
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


def test_same_source_version_is_reused_or_merged_without_duplicate_tasks(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        provider="civitai",
        repo_id="models/123",
        requested_revision="latest",
        commit_hash="456",
        files=[
            RepoFileInfo(path="fp16.safetensors", size=10),
            RepoFileInfo(path="fp8.safetensors", size=5),
        ],
        total_bytes=15,
    )

    original = manager.create_task(resolution, ["fp16.safetensors"], None)
    reused = manager.create_task(resolution, ["fp16.safetensors"], None)
    merged = manager.create_task(resolution, ["fp8.safetensors"], None)

    assert reused["id"] == original["id"]
    assert merged["id"] != original["id"]
    assert {file["path"] for file in merged["files"]} == {
        "fp16.safetensors",
        "fp8.safetensors",
    }
    assert [task["id"] for task in manager.db.list_tasks()] == [merged["id"]]


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


def test_dataset_identity_destination_and_restart_token_recovery(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    commit = "d" * 40
    dataset = RepoResolution(
        repo_id="owner/shared-name",
        repo_type="dataset",
        requested_revision="main",
        commit_hash=commit,
        files=[RepoFileInfo(path="data/train.parquet", size=10)],
        total_bytes=10,
    )
    model = RepoResolution(
        repo_id="owner/shared-name",
        repo_type="model",
        requested_revision="main",
        commit_hash=commit,
        files=[RepoFileInfo(path="model.bin", size=10)],
        total_bytes=10,
    )

    dataset_task = manager.create_task(dataset, ["data/train.parquet"], "hf_private")
    model_task = manager.create_task(model, ["model.bin"], None)

    assert dataset_task["repo_type"] == "dataset"
    assert dataset_task["destination"] == f"datasets/owner/shared-name/{commit}"
    assert model_task["destination"] == f"models/owner/shared-name/{commit}"
    assert manager.task_destination(dataset_task) != manager.task_destination(model_task)

    restarted = DownloadManager(manager.paths, manager.db, manager.broker)
    with pytest.raises(DownloadManagerError, match="重新提供 Hugging Face token"):
        restarted.resume(dataset_task["id"])
    resumed = restarted.resume(dataset_task["id"], token="hf_again")
    assert resumed["status"] == "queued"
    assert b"hf_again" not in manager.paths.database.read_bytes()


def test_dataset_runnable_task_includes_repo_type_for_dispatch(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    dataset = RepoResolution(
        repo_id="inlineresearch/krea2-skin-lora",
        repo_type="dataset",
        requested_revision="main",
        commit_hash="d" * 40,
        files=[RepoFileInfo(path="README.md", size=10)],
        total_bytes=10,
    )
    created = manager.create_task(dataset, ["README.md"], None)

    runnable = manager.db.runnable_tasks()

    assert len(runnable) == 1
    assert runnable[0]["id"] == created["id"]
    assert runnable[0]["provider"] == "huggingface"
    assert runnable[0]["repo_type"] == "dataset"


def test_coordinator_dispatches_dataset_worker(tmp_path: Path, monkeypatch) -> None:
    manager = make_manager(tmp_path)
    dataset = RepoResolution(
        repo_id="inlineresearch/krea2-skin-lora",
        repo_type="dataset",
        requested_revision="main",
        commit_hash="d" * 40,
        files=[RepoFileInfo(path="README.md", size=10)],
        total_bytes=10,
    )
    created = manager.create_task(dataset, ["README.md"], None)
    dispatched: list[dict] = []

    def fake_spawn(task, file, key):
        dispatched.append(task)
        return ActiveWorker(task["id"], file["id"], key, None)  # type: ignore[arg-type]

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(manager, "_spawn", fake_spawn)
    monkeypatch.setattr("hfdm.download_manager.threading.Thread", FakeThread)

    manager._dispatch()

    assert dispatched[0]["repo_type"] == "dataset"
    assert manager.db.get_task(created["id"])["status"] == "downloading"  # type: ignore[index]


def test_civitai_identity_metadata_hash_and_destination_are_persisted(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    token = "civitai_memory_only"
    resolution = RepoResolution(
        provider="civitai",
        repo_id="models/123",
        repo_type="model",
        requested_revision="latest",
        commit_hash="456",
        display_name="Example LoRA",
        provider_metadata={"model_id": 123, "version_id": 456},
        files=[
            RepoFileInfo(
                path="example.safetensors",
                size=10,
                remote_id="789",
                sha256="a" * 64,
                provider_metadata={
                    "download_url": "https://civitai.com/api/download/models/456"
                },
            )
        ],
        total_bytes=10,
    )

    task = manager.create_task(resolution, ["example.safetensors"], token)

    assert task["provider"] == "civitai"
    assert task["display_name"] == "Example LoRA"
    assert task["destination"] == "civitai/models/123/456"
    assert manager.task_destination(task) == (tmp_path / "download/civitai/models/123/456").resolve()
    assert task["files"][0]["remote_id"] == "789"
    assert task["files"][0]["expected_sha256"] == "a" * 64
    assert task["files"][0]["provider_metadata"]["download_url"].startswith(
        "https://civitai.com/api/download/models/"
    )
    assert token.encode() not in manager.paths.database.read_bytes()


def test_civitai_worker_auth_error_changes_task_to_auth_required(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        provider="civitai",
        repo_id="models/123",
        repo_type="model",
        requested_revision="latest",
        commit_hash="456",
        files=[
            RepoFileInfo(
                path="private.bin",
                size=3,
                remote_id="789",
                provider_metadata={
                    "download_url": "https://civitai.com/api/download/models/456"
                },
            )
        ],
        total_bytes=3,
    )
    task = manager.create_task(resolution, ["private.bin"], None)
    file = task["files"][0]

    class FailedProcess:
        stdout = io.StringIO(
            '{"type":"error","error":"token required","kind":"CivitaiAuthRequired"}\n'
        )
        stderr = io.StringIO("")

        @staticmethod
        def wait():
            return 1

    worker = ActiveWorker(task["id"], file["id"], "civitai:key", FailedProcess())  # type: ignore[arg-type]
    manager._active[worker.key] = worker
    manager._monitor(worker, 3)

    failed = manager.db.get_task(task["id"])
    assert failed is not None
    assert failed["status"] == "auth_required"
    assert failed["requires_token"] is True
    assert failed["files"][0]["status"] == "paused"


def test_civitai_unknown_media_size_updates_task_totals_from_worker(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    resolution = RepoResolution(
        provider="civitai",
        repo_id="models/123",
        repo_type="model",
        requested_revision="latest",
        commit_hash="456",
        files=[
            RepoFileInfo(
                path="examples/preview.jpeg",
                remote_id="image:1",
                provider_metadata={
                    "kind": "example_image",
                    "download_url": "https://image.civitai.com/preview.jpeg",
                },
            )
        ],
        total_bytes=0,
    )
    task = manager.create_task(resolution, ["examples/preview.jpeg"], None)
    file = task["files"][0]

    class SuccessfulProcess:
        stdout = io.StringIO('{"type":"progress","downloaded":12,"total":12}\n')
        stderr = io.StringIO("")

        @staticmethod
        def wait():
            return 0

    worker = ActiveWorker(task["id"], file["id"], "civitai:image", SuccessfulProcess())  # type: ignore[arg-type]
    manager._active[worker.key] = worker
    manager._monitor(worker, 0)

    completed = manager.db.get_task(task["id"])
    assert completed is not None
    assert completed["total_bytes"] == 12
    assert completed["downloaded_bytes"] == 12
    assert completed["files"][0]["size"] == 12
