from pathlib import Path

from hfdm.database import Database, utc_now


def test_settings_use_benchmarked_defaults_and_persist_hf_profile(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.sqlite3")
    db.initialize()

    settings = db.get_settings()
    assert settings["max_concurrent_files"] == 8
    assert settings["civitai_segments"] == 1
    assert settings["hf_profile"] == "balanced"

    db.set_settings({**settings, "hf_profile": "hdd"})
    assert db.get_settings()["hf_profile"] == "hdd"


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


def test_library_exposes_record_and_file_provider_metadata(tmp_path: Path) -> None:
    db = Database(tmp_path / "data.sqlite3")
    db.initialize()
    now = utc_now()
    db.create_task(
        {
            "id": "civitai-task",
            "provider": "civitai",
            "repo_type": "model",
            "repo_id": "models/123",
            "requested_revision": "latest",
            "commit_hash": "456",
            "destination": str(tmp_path / "downloads"),
            "status": "completed",
            "total_bytes": 10,
            "requires_token": 0,
            "display_name": "Example LoRA",
            "provider_metadata": {
                "model_type": "LORA",
                "base_model": "SDXL 1.0",
                "tags": ["style"],
            },
            "created_at": now,
            "updated_at": now,
        },
        [
            {
                "id": "civitai-file",
                "task_id": "civitai-task",
                "path": "example.safetensors",
                "size": 10,
                "status": "completed",
                "provider_metadata": {
                    "kind": "model",
                    "comfyui_folder": "loras",
                    "comfyui_path": "ComfyUI/models/loras",
                },
            }
        ],
    )

    item = db.list_library_items()[0]

    assert item["provider_metadata"]["model_type"] == "LORA"
    assert item["provider_metadata"]["tags"] == ["style"]
    assert item["files"][0]["provider_metadata"]["comfyui_folder"] == "loras"
