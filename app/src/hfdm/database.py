from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import DEFAULT_SETTINGS


SCHEMA_VERSION = 3
TERMINAL_TRANSFER_STATUSES = {"completed", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        if existed and self._requires_v2_migration():
            self._backup_database(self._source_schema_version())
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    requested_revision TEXT NOT NULL,
                    commit_hash TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    speed_bps REAL NOT NULL DEFAULT 0,
                    eta_seconds INTEGER,
                    requires_token INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_files (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                    remote_id TEXT,
                    expected_sha256 TEXT,
                    download_url TEXT,
                    provider_metadata TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    UNIQUE(task_id, path)
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_task_files_task_status
                    ON task_files(task_id, status);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "speed_bps" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN speed_bps REAL NOT NULL DEFAULT 0")
            if "eta_seconds" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN eta_seconds INTEGER")
            current_version = self._schema_version(conn)
            if current_version < SCHEMA_VERSION:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    self._migrate_to_v2(conn)
                except Exception:
                    conn.rollback()
                    raise
                else:
                    conn.commit()
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            conn.execute("PRAGMA optimize")

    @property
    def v1_backup_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.v1.bak")

    @property
    def v2_backup_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.v2.bak")

    def _source_schema_version(self) -> int:
        with sqlite3.connect(self.path) as conn:
            return self._schema_version(conn)

    def _requires_v2_migration(self) -> bool:
        with sqlite3.connect(self.path) as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "tasks" not in tables:
                return False
            if "schema_version" not in tables:
                return True
            row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
            return row is None or int(row[0]) < SCHEMA_VERSION

    def _backup_database(self, source_version: int) -> None:
        backup_path = self.path.with_name(f"{self.path.name}.v{source_version}.bak")
        if backup_path.exists():
            return
        with sqlite3.connect(self.path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if not exists:
            return 1
        row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        return int(row[0]) if row else 1

    def _migrate_to_v2(self, conn: sqlite3.Connection) -> None:
        statements = (
            "CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)",
            """
            CREATE TABLE IF NOT EXISTS download_records (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                repo_type TEXT NOT NULL,
                remote_id TEXT NOT NULL,
                requested_revision TEXT NOT NULL,
                resolved_revision TEXT NOT NULL,
                destination TEXT NOT NULL,
                transfer_status TEXT NOT NULL,
                local_availability TEXT NOT NULL DEFAULT 'unknown',
                total_bytes INTEGER NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                speed_bps REAL NOT NULL DEFAULT 0,
                eta_seconds INTEGER,
                requires_token INTEGER NOT NULL DEFAULT 0,
                display_name TEXT,
                provider_metadata TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                last_reconciled_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS download_attempts (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL REFERENCES download_records(id) ON DELETE CASCADE,
                attempt_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                average_speed_bps REAL NOT NULL DEFAULT 0,
                peak_speed_bps REAL NOT NULL DEFAULT 0,
                error TEXT,
                UNIQUE(record_id, attempt_number)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS download_record_files (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL REFERENCES download_records(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                expected_size INTEGER NOT NULL DEFAULT 0,
                expected_sha256 TEXT,
                remote_id TEXT,
                download_url TEXT,
                provider_metadata TEXT NOT NULL DEFAULT '{}',
                transfer_status TEXT NOT NULL,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                local_status TEXT NOT NULL DEFAULT 'unknown',
                observed_size INTEGER,
                observed_mtime_ns INTEGER,
                observed_sha256 TEXT,
                last_reconciled_at TEXT,
                error TEXT,
                UNIQUE(record_id, path)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_download_records_transfer ON download_records(transfer_status)",
            "CREATE INDEX IF NOT EXISTS idx_download_records_availability ON download_records(local_availability)",
            "CREATE INDEX IF NOT EXISTS idx_download_attempts_record ON download_attempts(record_id, attempt_number)",
            "CREATE INDEX IF NOT EXISTS idx_download_record_files_record ON download_record_files(record_id, local_status)",
        )
        for statement in statements:
            conn.execute(statement)

        self._migrate_to_v3(conn)

        task_rows = conn.execute("SELECT * FROM tasks").fetchall()
        for task in task_rows:
            transfer_status = "failed" if task["status"] == "partial" else task["status"]
            completed_at = task["updated_at"] if transfer_status in TERMINAL_TRANSFER_STATUSES else None
            conn.execute(
                """
                INSERT OR IGNORE INTO download_records(
                    id, provider, repo_type, remote_id, requested_revision, resolved_revision,
                    destination, transfer_status, local_availability, total_bytes,
                    downloaded_bytes, speed_bps, eta_seconds, requires_token, error,
                    created_at, updated_at, completed_at
                ) VALUES (?, 'huggingface', 'model', ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"], task["repo_id"], task["requested_revision"], task["commit_hash"],
                    task["destination"], transfer_status, task["total_bytes"], task["downloaded_bytes"],
                    task["speed_bps"], task["eta_seconds"], task["requires_token"], task["error"],
                    task["created_at"], task["updated_at"], completed_at,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO download_attempts(
                    id, record_id, attempt_number, status, started_at, completed_at,
                    total_bytes, downloaded_bytes, average_speed_bps, peak_speed_bps, error
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{task['id']}:1", task["id"], transfer_status, task["created_at"], completed_at,
                    task["total_bytes"], task["downloaded_bytes"], task["speed_bps"],
                    task["speed_bps"], task["error"],
                ),
            )
        conn.execute(
            """
            INSERT OR IGNORE INTO download_record_files(
                id, record_id, path, expected_size, transfer_status, downloaded_bytes, error
            )
            SELECT id, task_id, path, size, status, downloaded_bytes, error FROM task_files
            """
        )
        conn.execute(
            "INSERT INTO schema_version(id, version) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET version=excluded.version",
            (SCHEMA_VERSION,),
        )

    @staticmethod
    def _migrate_to_v3(conn: sqlite3.Connection) -> None:
        additions = {
            "task_files": {
                "remote_id": "TEXT",
                "expected_sha256": "TEXT",
                "download_url": "TEXT",
                "provider_metadata": "TEXT NOT NULL DEFAULT '{}'",
            },
            "download_records": {
                "display_name": "TEXT",
                "provider_metadata": "TEXT NOT NULL DEFAULT '{}'",
            },
            "download_record_files": {
                "remote_id": "TEXT",
                "download_url": "TEXT",
                "provider_metadata": "TEXT NOT NULL DEFAULT '{}'",
            },
        }
        for table, columns in additions.items():
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, declaration in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def recover_interrupted(self) -> None:
        now = utc_now()
        with self._write_lock, self.connect() as conn:
            rows = conn.execute(
                "SELECT id, requires_token FROM tasks WHERE status IN ('downloading', 'pausing', 'queued')"
            ).fetchall()
            for row in rows:
                status = "auth_required" if row["requires_token"] else "paused"
                conn.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                    (status, now, row["id"]),
                )
                conn.execute(
                    "UPDATE task_files SET status=? WHERE task_id=? AND status IN ('downloading', 'queued')",
                    ("paused", row["id"]),
                )
                conn.execute(
                    "UPDATE download_records SET transfer_status=?, updated_at=? WHERE id=?",
                    (status, now, row["id"]),
                )
                conn.execute(
                    "UPDATE download_record_files SET transfer_status='paused' "
                    "WHERE record_id=? AND transfer_status IN ('downloading', 'queued')",
                    (row["id"],),
                )
                conn.execute(
                    "UPDATE download_attempts SET status=? WHERE id=("
                    "SELECT id FROM download_attempts WHERE record_id=? ORDER BY attempt_number DESC LIMIT 1)",
                    (status, row["id"]),
                )

    def create_task(self, task: dict[str, Any], files: Iterable[dict[str, Any]]) -> None:
        file_rows = [
            {
                "remote_id": None,
                "expected_sha256": None,
                "download_url": None,
                "provider_metadata": "{}",
                **file,
                "provider_metadata": self._json_text(file.get("provider_metadata", {})),
            }
            for file in files
        ]
        task_values = {
            "provider": "huggingface",
            "repo_type": "model",
            "display_name": None,
            "provider_metadata": "{}",
            **task,
            "provider_metadata": self._json_text(task.get("provider_metadata", {})),
        }
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks(
                    id, repo_id, requested_revision, commit_hash, destination, status,
                    total_bytes, downloaded_bytes, requires_token, error, created_at, updated_at
                ) VALUES (
                    :id, :repo_id, :requested_revision, :commit_hash, :destination, :status,
                    :total_bytes, 0, :requires_token, NULL, :created_at, :updated_at
                )
                """,
                task_values,
            )
            conn.executemany(
                """
                INSERT INTO task_files(
                    id, task_id, path, size, status, downloaded_bytes, remote_id,
                    expected_sha256, download_url, provider_metadata, error
                ) VALUES (
                    :id, :task_id, :path, :size, :status, 0, :remote_id,
                    :expected_sha256, :download_url, :provider_metadata, NULL
                )
                """,
                file_rows,
            )
            conn.execute(
                """
                INSERT INTO download_records(
                    id, provider, repo_type, remote_id, requested_revision, resolved_revision,
                    destination, transfer_status, local_availability, total_bytes,
                    downloaded_bytes, requires_token, display_name, provider_metadata,
                    error, created_at, updated_at
                ) VALUES (
                    :id, :provider, :repo_type, :repo_id, :requested_revision, :commit_hash,
                    :destination, :status, 'unknown', :total_bytes, 0, :requires_token,
                    :display_name, :provider_metadata, NULL, :created_at, :updated_at
                )
                """,
                task_values,
            )
            conn.execute(
                """
                INSERT INTO download_attempts(
                    id, record_id, attempt_number, status, started_at, total_bytes
                ) VALUES (?, ?, 1, ?, ?, ?)
                """,
                (f"{task['id']}:1", task["id"], task["status"], task["created_at"], task["total_bytes"]),
            )
            conn.executemany(
                """
                INSERT INTO download_record_files(
                    id, record_id, path, expected_size, expected_sha256, remote_id,
                    download_url, provider_metadata, transfer_status, downloaded_bytes, error
                ) VALUES (
                    :id, :task_id, :path, :size, :expected_sha256, :remote_id,
                    :download_url, :provider_metadata, :status, 0, NULL
                )
                """,
                file_rows,
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            task_rows = conn.execute("SELECT * FROM download_records ORDER BY created_at DESC").fetchall()
            return [self._task_with_files(conn, row) for row in task_rows]

    def list_library_items(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            records = conn.execute(
                "SELECT * FROM download_records ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
            groups: dict[tuple[str, str, str, str, str], list[sqlite3.Row]] = {}
            for record in records:
                key = (
                    record["provider"],
                    record["repo_type"],
                    record["remote_id"],
                    record["resolved_revision"],
                    record["destination"],
                )
                groups.setdefault(key, []).append(record)

            items: list[dict[str, Any]] = []
            local_priority = {"available": 4, "changed": 3, "unknown": 2, "moved": 1}
            for identity, group_records in groups.items():
                files_by_path: dict[str, dict[str, Any]] = {}
                for record in group_records:
                    file_rows = conn.execute(
                        """
                        SELECT * FROM download_record_files
                        WHERE record_id=? AND (
                            transfer_status='completed'
                            OR local_status IN ('available', 'moved', 'changed')
                        )
                        ORDER BY path
                        """,
                        (record["id"],),
                    ).fetchall()
                    for row in file_rows:
                        candidate = {
                            "record_id": record["id"],
                            "id": row["id"],
                            "path": row["path"],
                            "size": row["expected_size"],
                            "local_status": row["local_status"],
                            "observed_size": row["observed_size"],
                        }
                        current = files_by_path.get(row["path"])
                        if current is None or local_priority.get(candidate["local_status"], 0) > local_priority.get(current["local_status"], 0):
                            files_by_path[row["path"]] = candidate
                if not files_by_path:
                    continue

                files = sorted(files_by_path.values(), key=lambda item: item["path"].casefold())
                completed_record_ids = {
                    record["id"]
                    for record in group_records
                    if record["transfer_status"] == "completed"
                }
                restore_record_ids = {
                    file["record_id"]
                    for file in files
                    if file["local_status"] == "moved"
                    and file["record_id"] in completed_record_ids
                }
                statuses = [file["local_status"] for file in files]
                if "changed" in statuses:
                    availability = "changed"
                elif "unknown" in statuses:
                    availability = "unknown"
                elif all(status == "available" for status in statuses):
                    availability = "available"
                elif all(status == "moved" for status in statuses):
                    availability = "moved"
                else:
                    availability = "partial"
                latest = group_records[0]
                items.append(
                    {
                        "key": "|".join(identity),
                        "provider": latest["provider"],
                        "repo_type": latest["repo_type"],
                        "repo_id": latest["remote_id"],
                        "requested_revision": latest["requested_revision"],
                        "commit_hash": latest["resolved_revision"],
                        "destination": latest["destination"],
                        "latest_record_id": latest["id"],
                        "latest_transfer_status": latest["transfer_status"],
                        "local_availability": availability,
                        "history_count": len(group_records),
                        "total_bytes": sum(int(file["size"]) for file in files),
                        "requires_token": any(bool(record["requires_token"]) for record in group_records),
                        "display_name": latest["display_name"],
                        "restore_record_ids": sorted(restore_record_ids),
                        "files": files,
                        "updated_at": latest["updated_at"],
                    }
                )
            items.sort(key=lambda item: item["updated_at"], reverse=True)
            for item in items:
                item.pop("updated_at", None)
            return items

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM download_records WHERE id=?", (task_id,)).fetchone()
            return self._task_with_files(conn, row) if row else None

    def _task_with_files(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["repo_id"] = task.pop("remote_id")
        task["commit_hash"] = task.pop("resolved_revision")
        task["status"] = task["transfer_status"]
        task["requires_token"] = bool(task["requires_token"])
        task["provider_metadata"] = self._json_object(task.get("provider_metadata"))
        task["files"] = [
            {
                **dict(item),
                "size": item["expected_size"],
                "status": item["transfer_status"],
                "provider_metadata": self._json_object(item["provider_metadata"]),
            }
            for item in conn.execute(
                "SELECT * FROM download_record_files WHERE record_id=? ORDER BY path", (task["id"],)
            ).fetchall()
        ]
        return task

    def runnable_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT tasks.*, download_records.provider, download_records.repo_type
                    FROM tasks
                    JOIN download_records ON download_records.id = tasks.id
                    WHERE tasks.status IN ('queued', 'downloading')
                    ORDER BY tasks.created_at
                    """
                ).fetchall()
            ]

    def next_file(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_files WHERE task_id=? AND status='queued' ORDER BY path LIMIT 1",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["provider_metadata"] = self._json_object(item.get("provider_metadata"))
            return item

    def get_file(self, task_id: str, path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM download_record_files WHERE record_id=? AND path=?", (task_id, path)
            ).fetchone()
            if not row:
                return None
            file = dict(row)
            file["size"] = file["expected_size"]
            file["status"] = file["transfer_status"]
            file["provider_metadata"] = self._json_object(file.get("provider_metadata"))
            return file

    def update_task(self, task_id: str, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {assignments} WHERE id=?",
                (*values.values(), task_id),
            )
            record_values = dict(values)
            if "status" in record_values:
                status = record_values.pop("status")
                record_values["transfer_status"] = "failed" if status == "partial" else status
                if record_values["transfer_status"] in TERMINAL_TRANSFER_STATUSES:
                    record_values["completed_at"] = values["updated_at"]
                else:
                    record_values["completed_at"] = None
            record_assignments = ", ".join(f"{key}=?" for key in record_values)
            conn.execute(
                f"UPDATE download_records SET {record_assignments} WHERE id=?",
                (*record_values.values(), task_id),
            )
            attempt_values = {
                ("status" if key == "status" else key): value
                for key, value in values.items()
                if key in {"status", "downloaded_bytes", "error"}
            }
            if attempt_values.get("status") == "partial":
                attempt_values["status"] = "failed"
            if "status" in attempt_values and attempt_values["status"] in TERMINAL_TRANSFER_STATUSES:
                attempt_values["completed_at"] = values["updated_at"]
            if attempt_values:
                attempt_assignments = ", ".join(f"{key}=?" for key in attempt_values)
                conn.execute(
                    f"UPDATE download_attempts SET {attempt_assignments} WHERE id=("
                    "SELECT id FROM download_attempts WHERE record_id=? ORDER BY attempt_number DESC LIMIT 1)",
                    (*attempt_values.values(), task_id),
                )

    def update_file(self, file_id: str, **values: Any) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                f"UPDATE task_files SET {assignments} WHERE id=?",
                (*values.values(), file_id),
            )
            record_values = dict(values)
            if "status" in record_values:
                record_values["transfer_status"] = record_values.pop("status")
            record_assignments = ", ".join(f"{key}=?" for key in record_values)
            conn.execute(
                f"UPDATE download_record_files SET {record_assignments} WHERE id=?",
                (*record_values.values(), file_id),
            )

    def update_file_size(self, file_id: str, size: int) -> None:
        with self._write_lock, self.connect() as conn:
            row = conn.execute("SELECT task_id FROM task_files WHERE id=?", (file_id,)).fetchone()
            if not row:
                return
            task_id = str(row["task_id"])
            conn.execute("UPDATE task_files SET size=? WHERE id=?", (size, file_id))
            conn.execute(
                "UPDATE download_record_files SET expected_size=? WHERE id=?",
                (size, file_id),
            )
            total = int(
                conn.execute(
                    "SELECT COALESCE(SUM(size), 0) FROM task_files WHERE task_id=?",
                    (task_id,),
                ).fetchone()[0]
            )
            now = utc_now()
            conn.execute(
                "UPDATE tasks SET total_bytes=?, updated_at=? WHERE id=?",
                (total, now, task_id),
            )
            conn.execute(
                "UPDATE download_records SET total_bytes=?, updated_at=? WHERE id=?",
                (total, now, task_id),
            )
            conn.execute(
                "UPDATE download_attempts SET total_bytes=? WHERE id=("
                "SELECT id FROM download_attempts WHERE record_id=? ORDER BY attempt_number DESC LIMIT 1)",
                (total, task_id),
            )

    def bulk_file_status(self, task_id: str, from_statuses: list[str], status: str) -> None:
        marks = ",".join("?" for _ in from_statuses)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                f"UPDATE task_files SET status=? WHERE task_id=? AND status IN ({marks})",
                (status, task_id, *from_statuses),
            )
            conn.execute(
                f"UPDATE download_record_files SET transfer_status=? "
                f"WHERE record_id=? AND transfer_status IN ({marks})",
                (status, task_id, *from_statuses),
            )

    def recompute_progress(self, task_id: str) -> tuple[int, int]:
        with self._write_lock, self.connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(downloaded_bytes), 0) downloaded,
                       COALESCE(SUM(size), 0) total
                FROM task_files WHERE task_id=?
                """,
                (task_id,),
            ).fetchone()
            downloaded = min(int(row["downloaded"]), int(row["total"]))
            conn.execute(
                "UPDATE tasks SET downloaded_bytes=?, updated_at=? WHERE id=?",
                (downloaded, utc_now(), task_id),
            )
            conn.execute(
                "UPDATE download_records SET downloaded_bytes=?, updated_at=? WHERE id=?",
                (downloaded, utc_now(), task_id),
            )
            conn.execute(
                "UPDATE download_attempts SET downloaded_bytes=? WHERE id=("
                "SELECT id FROM download_attempts WHERE record_id=? ORDER BY attempt_number DESC LIMIT 1)",
                (downloaded, task_id),
            )
            return downloaded, int(row["total"])

    def file_status_counts(self, task_id: str) -> dict[str, int]:
        with self.connect() as conn:
            return {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) count FROM task_files WHERE task_id=? GROUP BY status",
                    (task_id,),
                ).fetchall()
            }

    def get_settings(self) -> dict[str, int]:
        with self.connect() as conn:
            values = {
                row["key"]: int(json.loads(row["value"]))
                for row in conn.execute("SELECT key, value FROM settings").fetchall()
            }
        return {**DEFAULT_SETTINGS, **values}

    def set_settings(self, values: dict[str, int]) -> None:
        with self._write_lock, self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )

    def destination_reference_count(self, destination: str, excluding_task_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) count FROM tasks WHERE destination=? AND id<>?",
                (destination, excluding_task_id),
            ).fetchone()
            return int(row["count"])

    def list_task_locations(self) -> list[dict[str, str]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, repo_type, remote_id AS repo_id,
                           resolved_revision AS commit_hash, destination, provider
                    FROM download_records
                    """
                ).fetchall()
            ]

    def set_task_destination(self, task_id: str, destination: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "UPDATE tasks SET destination=? WHERE id=?",
                (destination, task_id),
            )
            conn.execute(
                "UPDATE download_records SET destination=? WHERE id=?",
                (destination, task_id),
            )

    def delete_task(self, task_id: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            conn.execute("DELETE FROM download_records WHERE id=?", (task_id,))

    def begin_retry_attempt(self, task_id: str) -> None:
        now = utc_now()
        with self._write_lock, self.connect() as conn:
            record = conn.execute(
                "SELECT total_bytes FROM download_records WHERE id=?", (task_id,)
            ).fetchone()
            if not record:
                return
            number = int(
                conn.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM download_attempts WHERE record_id=?",
                    (task_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO download_attempts(
                    id, record_id, attempt_number, status, started_at, total_bytes
                ) VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (f"{task_id}:{number}", task_id, number, now, record["total_bytes"]),
            )

    def prepare_missing_redownload(self, task_id: str) -> list[str]:
        now = utc_now()
        with self._write_lock, self.connect() as conn:
            record = conn.execute(
                """
                SELECT id, total_bytes, transfer_status, local_availability
                FROM download_records WHERE id=?
                """,
                (task_id,),
            ).fetchone()
            if not record:
                raise ValueError("Download history was not found")
            if record["transfer_status"] != "completed":
                raise ValueError("Only completed download history can restore moved files")
            if record["local_availability"] not in {"moved", "partial"}:
                raise ValueError("This download has no moved files to restore")
            missing = conn.execute(
                """
                SELECT id, path FROM download_record_files
                WHERE record_id=? AND transfer_status='completed' AND local_status='moved'
                ORDER BY path
                """,
                (task_id,),
            ).fetchall()
            if not missing:
                raise ValueError("This download has no moved files to restore")

            number = int(
                conn.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM download_attempts WHERE record_id=?",
                    (task_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO download_attempts(
                    id, record_id, attempt_number, status, started_at, total_bytes
                ) VALUES (?, ?, ?, 'queued', ?, ?)
                """,
                (f"{task_id}:{number}", task_id, number, now, record["total_bytes"]),
            )
            file_ids = [row["id"] for row in missing]
            marks = ",".join("?" for _ in file_ids)
            conn.execute(
                f"UPDATE task_files SET status='queued', downloaded_bytes=0, error=NULL "
                f"WHERE task_id=? AND id IN ({marks})",
                (task_id, *file_ids),
            )
            conn.execute(
                f"UPDATE download_record_files "
                f"SET transfer_status='queued', downloaded_bytes=0, error=NULL "
                f"WHERE record_id=? AND id IN ({marks})",
                (task_id, *file_ids),
            )
            progress = conn.execute(
                "SELECT COALESCE(SUM(downloaded_bytes), 0) FROM task_files WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE tasks SET status='queued', downloaded_bytes=?, speed_bps=0,
                                 eta_seconds=NULL, error=NULL, updated_at=?
                WHERE id=?
                """,
                (progress, now, task_id),
            )
            conn.execute(
                """
                UPDATE download_records
                SET transfer_status='queued', downloaded_bytes=?, speed_bps=0,
                    eta_seconds=NULL, error=NULL, completed_at=NULL, updated_at=?
                WHERE id=?
                """,
                (progress, now, task_id),
            )
            conn.execute(
                "UPDATE download_attempts SET downloaded_bytes=? WHERE id=?",
                (progress, f"{task_id}:{number}"),
            )
            return [row["path"] for row in missing]

    def list_attempts(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM download_attempts WHERE record_id=? ORDER BY attempt_number",
                    (task_id,),
                ).fetchall()
            ]

    def replace_editable_task_files(
        self,
        task_id: str,
        files: Iterable[dict[str, Any]],
        *,
        requires_token: bool | None,
    ) -> None:
        file_rows = [
            {
                "remote_id": None,
                "expected_sha256": None,
                "download_url": None,
                "provider_metadata": "{}",
                **file,
                "provider_metadata": self._json_text(file.get("provider_metadata", {})),
            }
            for file in files
        ]
        now = utc_now()
        total = sum(int(file["size"]) for file in file_rows)
        downloaded = sum(int(file["downloaded_bytes"]) for file in file_rows)
        with self._write_lock, self.connect() as conn:
            task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("Download task was not found")
            if task["status"] not in {"queued", "paused", "auth_required"}:
                raise ValueError("This task cannot be edited in place")
            conn.execute("DELETE FROM task_files WHERE task_id=?", (task_id,))
            conn.execute("DELETE FROM download_record_files WHERE record_id=?", (task_id,))
            conn.executemany(
                """
                INSERT INTO task_files(
                    id, task_id, path, size, status, downloaded_bytes, remote_id,
                    expected_sha256, download_url, provider_metadata, error
                ) VALUES (
                    :id, :task_id, :path, :size, :status, :downloaded_bytes, :remote_id,
                    :expected_sha256, :download_url, :provider_metadata, :error
                )
                """,
                file_rows,
            )
            conn.executemany(
                """
                INSERT INTO download_record_files(
                    id, record_id, path, expected_size, transfer_status,
                    downloaded_bytes, local_status, observed_size,
                    observed_mtime_ns, observed_sha256, last_reconciled_at, error,
                    expected_sha256, remote_id, download_url, provider_metadata
                ) VALUES (
                    :id, :task_id, :path, :size, :status,
                    :downloaded_bytes, :local_status, :observed_size,
                    :observed_mtime_ns, :observed_sha256, :last_reconciled_at, :error,
                    :expected_sha256, :remote_id, :download_url, :provider_metadata
                )
                """,
                file_rows,
            )
            token_sql = ", requires_token=?" if requires_token is not None else ""
            token_values: tuple[Any, ...] = (int(requires_token),) if requires_token is not None else ()
            conn.execute(
                f"UPDATE tasks SET total_bytes=?, downloaded_bytes=?, updated_at=?{token_sql} WHERE id=?",
                (total, downloaded, now, *token_values, task_id),
            )
            conn.execute(
                f"UPDATE download_records SET total_bytes=?, downloaded_bytes=?, updated_at=?{token_sql} WHERE id=?",
                (total, downloaded, now, *token_values, task_id),
            )
            conn.execute(
                """
                UPDATE download_attempts SET total_bytes=?, downloaded_bytes=?
                WHERE id=(SELECT id FROM download_attempts WHERE record_id=?
                          ORDER BY attempt_number DESC LIMIT 1)
                """,
                (total, downloaded, task_id),
            )

    def list_reconciliation_records(self, record_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            params: tuple[str, ...] = (record_id,) if record_id else ()
            where = "WHERE id=?" if record_id else ""
            records = conn.execute(
                f"SELECT id, destination, transfer_status, local_availability FROM download_records {where}",
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            for record in records:
                item = dict(record)
                item["files"] = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT id, path, expected_size, expected_sha256, transfer_status,
                               local_status, observed_size, observed_mtime_ns, observed_sha256
                        FROM download_record_files WHERE record_id=? ORDER BY path
                        """,
                        (record["id"],),
                    ).fetchall()
                ]
                result.append(item)
            return result

    def apply_reconciliation(
        self,
        record_id: str,
        availability: str,
        observations: Iterable[dict[str, Any]],
        reconciled_at: str,
    ) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "UPDATE download_records SET local_availability=?, last_reconciled_at=? WHERE id=?",
                (availability, reconciled_at, record_id),
            )
            conn.executemany(
                """
                UPDATE download_record_files
                SET local_status=:local_status,
                    observed_size=:observed_size,
                    observed_mtime_ns=:observed_mtime_ns,
                    observed_sha256=:observed_sha256,
                    last_reconciled_at=:last_reconciled_at
                WHERE id=:id AND record_id=:record_id
                """,
                observations,
            )

    def schema_version(self) -> int:
        with self.connect() as conn:
            return self._schema_version(conn)

    def expired_completed_tasks(self, updated_before: str) -> list[str]:
        with self.connect() as conn:
            return [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT tasks.id FROM tasks
                    JOIN download_records ON download_records.id=tasks.id
                    WHERE tasks.status='completed' AND tasks.updated_at<?
                      AND download_records.local_availability NOT IN ('moved', 'unknown')
                    ORDER BY tasks.updated_at
                    """,
                    (updated_before,),
                ).fetchall()
            ]

    @staticmethod
    def _json_text(value: Any) -> str:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = value
        return json.dumps(parsed if isinstance(parsed, dict) else {}, separators=(",", ":"))

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
