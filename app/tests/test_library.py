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
    provider: str = "huggingface",
    repo_type: str = "model",
    repo_id: str = "owner/repo",
    provider_metadata: dict[str, object] | None = None,
) -> None:
    now = utc_now()
    db.create_task(
        {
            "id": record_id,
            "provider": provider,
            "repo_type": repo_type,
            "repo_id": repo_id,
            "requested_revision": "main",
            "commit_hash": commit_hash,
            "destination": f"models/owner/repo/{commit_hash}",
            "status": "completed",
            "total_bytes": sum(size for _, size in files),
            "requires_token": 0,
            "provider_metadata": provider_metadata or {},
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


def test_archiving_library_item_removes_shared_content_but_keeps_history(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    commit = "a" * 40
    destination = paths.downloads / "models" / "owner" / "repo" / commit
    destination.mkdir(parents=True)
    (destination / "model.bin").write_bytes(b"model")
    add_completed_record(app.state.db, record_id="first", commit_hash=commit, files=[("model.bin", 5)])
    add_completed_record(app.state.db, record_id="second", commit_hash=commit, files=[("model.bin", 5)])
    app.state.manager.reconcile()

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/library/second/archive")
        library = client.get("/api/library").json()
        history = client.get("/api/tasks").json()

    assert response.status_code == 200
    assert response.json()["archived"] is True
    assert not destination.exists()
    assert len(history) == 2
    assert len(library) == 1
    assert library[0]["local_availability"] == "archived"
    assert library[0]["files"][0]["local_status"] == "archived"
    assert len(library[0]["restore_record_ids"]) == 1

    restored = app.state.manager.redownload_missing(library[0]["restore_record_ids"][0])
    assert restored["status"] == "queued"
    assert [file["path"] for file in restored["files"] if file["status"] == "queued"] == ["model.bin"]
    assert [attempt["attempt_number"] for attempt in app.state.db.list_attempts(restored["id"])] == [1, 2]


def test_user_tags_are_shared_by_source_and_can_be_managed(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(app.state.db, record_id="first", commit_hash="a" * 40, files=[("a.bin", 1)])
    add_completed_record(app.state.db, record_id="second", commit_hash="b" * 40, files=[("b.bin", 1)])

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        created = client.post("/api/user-tags", json={"name": "Favorite"})
        tag_id = created.json()["id"]
        attached = client.put(f"/api/library/first/user-tags/{tag_id}")
        items = client.get("/api/library").json()
        renamed = client.put(f"/api/user-tags/{tag_id}", json={"name": "Keep"})
        deleted = client.delete(f"/api/user-tags/{tag_id}")

    assert created.status_code == 201
    assert attached.status_code == 204
    assert len(items) == 2
    assert all(item["user_tags"][0]["name"] == "Favorite" for item in items)
    assert renamed.json()["name"] == "Keep"
    assert deleted.status_code == 204
    assert app.state.db.list_user_tags() == []


def test_archive_and_user_tag_writes_require_local_admin(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(app.state.db, record_id="first", commit_hash="a" * 40, files=[("a.bin", 1)])
    visitor = TestClient(app, client=("192.168.1.20", 50000))

    assert visitor.post("/api/library/first/archive").status_code == 403
    assert visitor.post("/api/library/first/refresh-source-date", json={}).status_code == 403
    assert visitor.post("/api/user-tags", json={"name": "private"}).status_code == 403


def test_library_timeline_date_defaults_from_source_and_is_editable(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(
        app.state.db,
        record_id="dated",
        commit_hash="a" * 40,
        files=[("model.bin", 5)],
        provider_metadata={"source_created_at": "2024-03-18T12:30:00+00:00"},
    )

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        initial = client.get("/api/library").json()[0]
        updated = client.put(
            "/api/library/dated/timeline-date",
            json={"timeline_date": "2024-02-01"},
        )
        edited = client.get("/api/library").json()[0]

    assert initial["source_created_at"] == "2024-03-18T12:30:00Z"
    assert initial["timeline_date"] == "2024-03-18"
    assert updated.status_code == 200
    assert edited["source_created_at"] == initial["source_created_at"]
    assert edited["timeline_date"] == "2024-02-01"
    assert edited["timeline_date_edited_at"] is not None


def test_refresh_huggingface_source_date_fills_undated_and_preserves_manual_date(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(app.state.db, record_id="automatic", commit_hash="a" * 40, files=[("a.bin", 1)])
    add_completed_record(app.state.db, record_id="manual", commit_hash="b" * 40, files=[("b.bin", 1)])
    app.state.db.set_library_timeline_date("manual", "2023-05-06")
    calls: list[tuple[str, str, str | None, str]] = []

    def source_dates(_service, repo_id, revision, token, repo_type):
        calls.append((repo_id, revision, token, repo_type))
        return "2024-07-08T12:30:00+00:00", "2024-08-09T10:00:00+00:00"

    monkeypatch.setattr("hfdm.hf_service.HuggingFaceService.source_dates", source_dates)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        automatic = client.post("/api/library/automatic/refresh-source-date", json={})
        manual = client.post("/api/library/manual/refresh-source-date", json={})
        preserved = {
            item["latest_record_id"]: item for item in client.get("/api/library").json()
        }
        restored = client.post(
            "/api/library/manual/refresh-source-date",
            json={"apply_source_date": True},
        )
        items = {item["latest_record_id"]: item for item in client.get("/api/library").json()}

    assert automatic.status_code == 200
    assert automatic.json()["timeline_date"] == "2024-07-08"
    assert automatic.json()["timeline_date_preserved"] is False
    assert manual.status_code == 200
    assert manual.json()["timeline_date"] == "2023-05-06"
    assert manual.json()["timeline_date_preserved"] is True
    assert manual.json()["timeline_date_restored"] is False
    assert preserved["manual"]["timeline_date"] == "2023-05-06"
    assert restored.status_code == 200
    assert restored.json()["timeline_date"] == "2024-07-08"
    assert restored.json()["timeline_date_preserved"] is False
    assert restored.json()["timeline_date_restored"] is True
    assert items["automatic"]["source_created_at"] == "2024-07-08T12:30:00Z"
    assert items["automatic"]["timeline_date"] == "2024-07-08"
    assert items["manual"]["source_created_at"] == "2024-07-08T12:30:00Z"
    assert items["manual"]["timeline_date"] == "2024-07-08"
    assert items["manual"]["timeline_date_edited_at"] is None
    assert calls == [
        ("owner/repo", "a" * 40, None, "model"),
        ("owner/repo", "b" * 40, None, "model"),
        ("owner/repo", "b" * 40, None, "model"),
    ]


def test_refresh_civitai_source_date_uses_original_version_id(tmp_path: Path, monkeypatch) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(
        app.state.db,
        record_id="civitai",
        commit_hash="456",
        files=[("model.safetensors", 1)],
        provider="civitai",
        repo_id="models/123",
    )
    calls: list[tuple[str, str, str | None]] = []

    def source_dates(_service, repo_id, version_id, token):
        calls.append((repo_id, version_id, token))
        return "2022-11-12T00:00:00Z", None

    monkeypatch.setattr("hfdm.civitai_service.CivitaiService.source_dates", source_dates)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/library/civitai/refresh-source-date",
            json={"civitai_token": "secret"},
        )

    assert response.status_code == 200
    assert response.json()["timeline_date"] == "2022-11-12"
    assert calls == [("models/123", "456", "secret")]


def test_refresh_source_date_reports_missing_created_at_without_changing_item(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(app.state.db, record_id="undated", commit_hash="a" * 40, files=[("a.bin", 1)])
    monkeypatch.setattr(
        "hfdm.hf_service.HuggingFaceService.source_dates",
        lambda *_args: (None, None),
    )

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/library/undated/refresh-source-date", json={})
        item = client.get("/api/library").json()[0]

    assert response.status_code == 422
    assert response.json()["detail"] == "模型來源沒有提供 createdAt"
    assert item["source_created_at"] is None
    assert item["timeline_date"] is None


def test_dashboard_counts_only_completed_attempts_and_reports_archive_space(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    app = create_app(paths)
    add_completed_record(
        app.state.db,
        record_id="model",
        commit_hash="a" * 40,
        files=[("model.bin", 5)],
    )
    add_completed_record(
        app.state.db,
        record_id="dataset",
        commit_hash="b" * 40,
        files=[("data.bin", 7)],
        repo_type="dataset",
        repo_id="owner/data",
    )
    add_completed_record(
        app.state.db,
        record_id="failed",
        commit_hash="c" * 40,
        files=[("failed.bin", 11)],
        provider="civitai",
        repo_id="models/123",
    )
    app.state.db.update_task("failed", status="failed")
    app.state.db.mark_library_archived("model")

    with TestClient(app) as client:
        response = client.get("/api/dashboard?days=90")

    assert response.status_code == 200
    body = response.json()
    assert body["download_count"] == 2
    assert body["unique_model_count"] == 2
    assert body["total_bytes"] == 12
    assert body["categories"] == {"Hugging Face Dataset": 1, "Hugging Face Model": 1}
    assert body["archived_model_count"] == 1
    assert body["archived_bytes"] == 5
    assert body["months"][0]["download_count"] == 2
