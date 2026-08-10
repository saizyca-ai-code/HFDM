from pathlib import Path

from hfdm.database import Database, utc_now


def test_database_creates_and_recovers_task(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.sqlite3")
    db.initialize()
    now = utc_now()
    db.create_task(
        {
            "id": "task",
            "repo_id": "owner/repo",
            "requested_revision": "main",
            "commit_hash": "a" * 40,
            "destination": str(tmp_path / "downloads"),
            "status": "downloading",
            "total_bytes": 10,
            "requires_token": 1,
            "created_at": now,
            "updated_at": now,
        },
        [{"id": "file", "task_id": "task", "path": "a.bin", "size": 10, "status": "downloading"}],
    )
    db.recover_interrupted()
    task = db.get_task("task")
    assert task is not None
    assert task["status"] == "auth_required"
    assert task["files"][0]["status"] == "paused"
