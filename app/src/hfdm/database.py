from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import DEFAULT_SETTINGS


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
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )
            conn.execute("PRAGMA optimize")

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

    def create_task(self, task: dict[str, Any], files: Iterable[dict[str, Any]]) -> None:
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
                task,
            )
            conn.executemany(
                """
                INSERT INTO task_files(id, task_id, path, size, status, downloaded_bytes, error)
                VALUES (:id, :task_id, :path, :size, :status, 0, NULL)
                """,
                files,
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            task_rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
            return [self._task_with_files(conn, row) for row in task_rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._task_with_files(conn, row) if row else None

    def _task_with_files(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["requires_token"] = bool(task["requires_token"])
        task["files"] = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM task_files WHERE task_id=? ORDER BY path", (task["id"],)
            ).fetchall()
        ]
        return task

    def runnable_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM tasks WHERE status IN ('queued', 'downloading') ORDER BY created_at"
                ).fetchall()
            ]

    def next_file(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_files WHERE task_id=? AND status='queued' ORDER BY path LIMIT 1",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_file(self, task_id: str, path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_files WHERE task_id=? AND path=?", (task_id, path)
            ).fetchone()
            return dict(row) if row else None

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

    def update_file(self, file_id: str, **values: Any) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                f"UPDATE task_files SET {assignments} WHERE id=?",
                (*values.values(), file_id),
            )

    def bulk_file_status(self, task_id: str, from_statuses: list[str], status: str) -> None:
        marks = ",".join("?" for _ in from_statuses)
        with self._write_lock, self.connect() as conn:
            conn.execute(
                f"UPDATE task_files SET status=? WHERE task_id=? AND status IN ({marks})",
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
                    "SELECT id, repo_id, commit_hash, destination FROM tasks"
                ).fetchall()
            ]

    def set_task_destination(self, task_id: str, destination: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "UPDATE tasks SET destination=? WHERE id=?",
                (destination, task_id),
            )

    def delete_task(self, task_id: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def expired_completed_tasks(self, updated_before: str) -> list[str]:
        with self.connect() as conn:
            return [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM tasks WHERE status='completed' AND updated_at<? ORDER BY updated_at",
                    (updated_before,),
                ).fetchall()
            ]
