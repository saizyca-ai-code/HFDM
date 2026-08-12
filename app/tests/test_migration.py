from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from hfdm.database import Database, SCHEMA_VERSION


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "v1"


def materialize_v1(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "data" / "hfdm.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as conn:
        conn.executescript((FIXTURE_ROOT / "hfdm-v1.sql").read_text(encoding="utf-8"))
    downloads = tmp_path / "download"
    shutil.copytree(FIXTURE_ROOT / "download", downloads)
    return database, downloads


def tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_v1_migration_creates_backup_and_preserves_download_tree(tmp_path: Path) -> None:
    database, downloads = materialize_v1(tmp_path)
    before = tree_digest(downloads)
    db = Database(database)

    db.initialize()

    assert db.schema_version() == SCHEMA_VERSION
    assert db.v1_backup_path.is_file()
    assert tree_digest(downloads) == before
    records = {item["id"]: item for item in db.list_tasks()}
    assert records["v1-available"]["provider"] == "huggingface"
    assert records["v1-available"]["repo_type"] == "model"
    assert records["v1-available"]["local_availability"] == "unknown"
    assert records["v1-failed"]["transfer_status"] == "failed"

    with sqlite3.connect(db.v1_backup_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "tasks" in tables
    assert "download_records" not in tables
    assert "schema_version" not in tables


def test_failed_migration_rolls_back_schema_and_keeps_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, downloads = materialize_v1(tmp_path)
    before = tree_digest(downloads)
    db = Database(database)

    def fail_after_ddl(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE migration_should_rollback(id INTEGER)")
        raise RuntimeError("fixture migration failure")

    monkeypatch.setattr(db, "_migrate_to_v2", fail_after_ddl)
    with pytest.raises(RuntimeError, match="fixture migration failure"):
        db.initialize()

    assert db.v1_backup_path.is_file()
    assert tree_digest(downloads) == before
    with sqlite3.connect(database) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "tasks" in tables
    assert "migration_should_rollback" not in tables
    assert "schema_version" not in tables
