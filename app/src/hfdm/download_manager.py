from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from .config import AppPaths
from .database import Database, utc_now
from .events import EventBroker
from .schemas import RepoResolution


class DownloadManagerError(RuntimeError):
    pass


@dataclass(slots=True)
class ActiveWorker:
    task_id: str
    file_id: str
    key: str
    process: subprocess.Popen[str]


class DownloadManager:
    def __init__(self, paths: AppPaths, db: Database, broker: EventBroker):
        self.paths = paths
        self.db = db
        self.broker = broker
        self._tokens: dict[str, str] = {}
        self._active: dict[str, ActiveWorker] = {}
        self._progress_state: dict[str, tuple[int, float, float]] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_cleanup = 0.0
        self._normalize_task_destinations()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.db.recover_interrupted()
        self._thread = threading.Thread(target=self._run, name="hfdm-coordinator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            workers = tuple(self._active.values())
        for worker in workers:
            self._terminate(worker.process)
        if self._thread:
            self._thread.join(timeout=5)

    def create_task(
        self,
        resolution: RepoResolution,
        selected_paths: list[str],
        token: str | None,
    ) -> dict[str, Any]:
        available = {item.path: item for item in resolution.files}
        selected = []
        seen: set[str] = set()
        for path in selected_paths:
            normalized = self._safe_relative(path).as_posix()
            if normalized in seen:
                continue
            if normalized not in available:
                raise DownloadManagerError(f"Repo 中找不到檔案：{normalized}")
            seen.add(normalized)
            selected.append(available[normalized])
        if not selected:
            raise DownloadManagerError("至少選擇一個檔案")

        owner, repo = resolution.repo_id.split("/", 1)
        destination_key = self._destination_key(owner, repo, resolution.commit_hash)
        destination = self._resolve_destination_key(destination_key)
        missing_bytes = sum(
            item.size for item in selected if not (destination / Path(item.path)).is_file()
        )
        self._assert_capacity(missing_bytes)

        now = utc_now()
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "repo_id": resolution.repo_id,
            "requested_revision": resolution.requested_revision,
            "commit_hash": resolution.commit_hash,
            "destination": destination_key,
            "status": "queued",
            "total_bytes": sum(item.size for item in selected),
            "requires_token": int(bool(token)),
            "created_at": now,
            "updated_at": now,
        }
        files = [
            {
                "id": str(uuid.uuid4()),
                "task_id": task_id,
                "path": item.path,
                "size": item.size,
                "status": "queued",
            }
            for item in selected
        ]
        self.db.create_task(task, files)
        if token:
            self._tokens[task_id] = token
        self._publish(task_id, "created")
        self._wake.set()
        created = self.db.get_task(task_id)
        assert created is not None
        return created

    def pause(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task["status"] in {"completed", "cancelled", "failed"}:
            raise DownloadManagerError("此任務目前無法暫停")
        self.db.update_task(task_id, status="pausing", error=None)
        self.db.bulk_file_status(task_id, ["queued"], "paused")
        with self._lock:
            workers = [worker for worker in self._active.values() if worker.task_id == task_id]
        for worker in workers:
            self._terminate(worker.process)
        if not workers:
            self.db.update_task(task_id, status="paused")
        self._publish(task_id, "pausing")
        return self._require_task(task_id)

    def resume(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task["status"] in {"completed", "cancelled"}:
            raise DownloadManagerError("此任務目前無法繼續")
        if token:
            self._tokens[task_id] = token
        if task["requires_token"] and task_id not in self._tokens:
            self.db.update_task(task_id, status="auth_required")
            raise DownloadManagerError("請重新提供 Hugging Face token")
        self.db.bulk_file_status(task_id, ["paused", "failed"], "queued")
        self.db.update_task(task_id, status="queued", error=None)
        self._publish(task_id, "resumed")
        self._wake.set()
        return self._require_task(task_id)

    def retry(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        return self.resume(task_id, token=token)

    def cancel(self, task_id: str) -> dict[str, Any]:
        self._require_task(task_id)
        self.db.update_task(task_id, status="cancelled")
        self.db.bulk_file_status(task_id, ["queued", "paused", "downloading", "failed"], "cancelled")
        with self._lock:
            workers = [worker for worker in self._active.values() if worker.task_id == task_id]
        for worker in workers:
            self._terminate(worker.process)
        self._tokens.pop(task_id, None)
        self._publish(task_id, "cancelled")
        return self._require_task(task_id)

    def delete(self, task_id: str, delete_files: bool = True) -> None:
        task = self._require_task(task_id)
        with self._lock:
            if any(worker.task_id == task_id for worker in self._active.values()):
                raise DownloadManagerError("下載中的任務不能刪除")
        if task["status"] in {"queued", "downloading", "pausing"}:
            raise DownloadManagerError("請先取消或暫停任務")
        settings = self.db.get_settings()
        if delete_files and not settings["allow_delete_files"]:
            raise DownloadManagerError("管理者設定目前禁止刪除實體檔案")
        destination = self.task_destination(task)
        if (
            delete_files
            and destination.exists()
            and self.db.destination_reference_count(task["destination"], task_id) == 0
        ):
            self._remove_tree(destination)
        self.db.delete_task(task_id)
        self._tokens.pop(task_id, None)
        self._publish(task_id, "deleted")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._dispatch()
                if time.monotonic() - self._last_cleanup >= 60:
                    self._cleanup_expired()
                    self._last_cleanup = time.monotonic()
            except Exception as exc:
                self.broker.publish({"type": "coordinator_error", "error": str(exc)})
            self._wake.wait(0.35)
            self._wake.clear()

    def _cleanup_expired(self) -> None:
        settings = self.db.get_settings()
        days = settings["retention_days"]
        if days <= 0 or not settings["allow_delete_files"]:
            return
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        for task_id in self.db.expired_completed_tasks(cutoff):
            try:
                self.delete(task_id, delete_files=True)
            except DownloadManagerError as exc:
                self.broker.publish({"type": "cleanup_error", "task_id": task_id, "error": str(exc)})

    def _dispatch(self) -> None:
        settings = self.db.get_settings()
        max_workers = settings["max_concurrent_files"]
        with self._lock:
            available_slots = max_workers - len(self._active)
        if available_slots <= 0:
            return

        for task in self.db.runnable_tasks():
            if available_slots <= 0:
                return
            task_id = task["id"]
            if task["requires_token"] and task_id not in self._tokens:
                self.db.update_task(task_id, status="auth_required")
                self._publish(task_id, "auth_required")
                continue
            file = self.db.next_file(task_id)
            if not file:
                self._finalize_task(task_id)
                continue

            destination = self.task_destination(task)
            target = (destination / Path(file["path"])).resolve()
            self._assert_inside(destination, target)
            if target.is_file() and target.stat().st_size == file["size"]:
                self.db.update_file(
                    file["id"], status="completed", downloaded_bytes=file["size"], error=None
                )
                self.db.recompute_progress(task_id)
                self._publish(task_id, "file_reused", file["id"])
                self._wake.set()
                continue

            key = f"{task['repo_id']}:{task['commit_hash']}:{file['path']}"
            with self._lock:
                if key in self._active:
                    continue
                worker = self._spawn(task, file, key)
                self._active[key] = worker
            self.db.update_file(file["id"], status="downloading", error=None)
            self.db.update_task(task_id, status="downloading", error=None)
            threading.Thread(
                target=self._monitor,
                args=(worker, file["size"]),
                name=f"hfdm-worker-{file['id'][:8]}",
                daemon=True,
            ).start()
            available_slots -= 1
            self._publish(task_id, "file_started", file["id"])

    def _spawn(self, task: dict[str, Any], file: dict[str, Any], key: str) -> ActiveWorker:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            [sys.executable, "-m", "hfdm.download_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=creation_flags,
        )
        payload = {
            "repo_id": task["repo_id"],
            "commit_hash": task["commit_hash"],
            "filename": file["path"],
            "destination": str(self.task_destination(task)),
            "token": self._tokens.get(task["id"]),
        }
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        return ActiveWorker(task["id"], file["id"], key, process)

    def _monitor(self, worker: ActiveWorker, expected_size: int) -> None:
        last_error: str | None = None
        assert worker.process.stdout is not None
        for line in worker.process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "progress":
                downloaded = min(int(event.get("downloaded", 0)), expected_size)
                now = time.monotonic()
                previous_bytes, previous_at, previous_speed = self._progress_state.get(
                    worker.file_id, (downloaded, now, 0.0)
                )
                elapsed = max(now - previous_at, 0.001)
                instant_speed = max(0.0, downloaded - previous_bytes) / elapsed
                speed = instant_speed if previous_speed <= 0 else previous_speed * 0.7 + instant_speed * 0.3
                self._progress_state[worker.file_id] = (downloaded, now, speed)
                self.db.update_file(worker.file_id, downloaded_bytes=downloaded)
                task_downloaded, task_total = self.db.recompute_progress(worker.task_id)
                with self._lock:
                    active_file_ids = [
                        item.file_id for item in self._active.values() if item.task_id == worker.task_id
                    ]
                task_speed = sum(self._progress_state.get(file_id, (0, 0.0, 0.0))[2] for file_id in active_file_ids)
                eta = int((task_total - task_downloaded) / task_speed) if task_speed > 0 else None
                self.db.update_task(worker.task_id, speed_bps=task_speed, eta_seconds=eta)
                self._publish(worker.task_id, "progress", worker.file_id)
            elif event.get("type") == "error":
                last_error = str(event.get("error") or "下載失敗")[:2000]

        return_code = worker.process.wait()
        if last_error is None and return_code != 0 and worker.process.stderr:
            last_error = worker.process.stderr.read().strip()[-2000:] or "下載 worker 已終止"
        with self._lock:
            self._active.pop(worker.key, None)
        self._progress_state.pop(worker.file_id, None)

        task = self.db.get_task(worker.task_id)
        if not task:
            return
        if return_code == 0:
            self.db.update_file(
                worker.file_id,
                status="completed",
                downloaded_bytes=expected_size,
                error=None,
            )
        elif task["status"] == "pausing":
            self.db.update_file(worker.file_id, status="paused", error=None)
        elif task["status"] == "cancelled":
            self.db.update_file(worker.file_id, status="cancelled", error=None)
        else:
            self.db.update_file(worker.file_id, status="failed", error=last_error)
            self.db.update_task(worker.task_id, error=last_error)
        self.db.recompute_progress(worker.task_id)
        self.db.update_task(worker.task_id, speed_bps=0, eta_seconds=None)
        self._finalize_task(worker.task_id)
        self._wake.set()

    def _finalize_task(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        counts = self.db.file_status_counts(task_id)
        with self._lock:
            has_active = any(worker.task_id == task_id for worker in self._active.values())
        if task["status"] == "cancelled":
            status = "cancelled"
        elif task["status"] == "pausing" and not has_active:
            status = "paused"
        elif counts.get("queued") or counts.get("downloading") or has_active:
            status = "downloading"
        elif counts.get("failed"):
            status = "partial" if counts.get("completed") else "failed"
        elif counts.get("paused"):
            status = "paused"
        else:
            status = "completed"
            self._tokens.pop(task_id, None)
        self.db.update_task(task_id, status=status)
        self._publish(task_id, status)

    def _assert_capacity(self, requested_bytes: int) -> None:
        settings = self.db.get_settings()
        self.paths.downloads.mkdir(parents=True, exist_ok=True)
        disk = shutil.disk_usage(self.paths.downloads)
        if requested_bytes > max(0, disk.free - settings["min_free_bytes"]):
            raise DownloadManagerError("磁碟剩餘空間不足")
        limit = settings["max_storage_bytes"]
        if limit:
            current = sum(
                path.stat().st_size
                for path in self.paths.downloads.rglob("*")
                if path.is_file()
            )
            if current + requested_bytes > limit:
                raise DownloadManagerError("下載會超過管理者設定的容量上限")

    def _require_task(self, task_id: str) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task:
            raise DownloadManagerError("找不到下載任務")
        return task

    def task_destination(self, task: dict[str, Any]) -> Path:
        owner, repo = task["repo_id"].split("/", 1)
        key = self._destination_key(owner, repo, task["commit_hash"])
        return self._resolve_destination_key(key)

    def _normalize_task_destinations(self) -> None:
        for task in self.db.list_task_locations():
            owner, repo = task["repo_id"].split("/", 1)
            destination = self._destination_key(owner, repo, task["commit_hash"])
            if task["destination"] != destination:
                self.db.set_task_destination(task["id"], destination)

    def _destination_key(self, owner: str, repo: str, commit_hash: str) -> str:
        return PurePosixPath(
            "models",
            self._safe_segment(owner),
            self._safe_segment(repo),
            self._safe_segment(commit_hash),
        ).as_posix()

    def _resolve_destination_key(self, key: str) -> Path:
        relative = self._safe_relative(key)
        destination = (self.paths.downloads / Path(*relative.parts)).resolve()
        self._assert_inside_download_root(destination)
        return destination

    def _publish(self, task_id: str, event: str, file_id: str | None = None) -> None:
        self.broker.publish({"type": event, "task_id": task_id, "file_id": file_id})

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

    @staticmethod
    def _safe_segment(value: str) -> str:
        if not value or value in {".", ".."} or any(char in value for char in "\\/:*?\"<>|"):
            raise DownloadManagerError("Repo 名稱無法安全映射到本機目錄")
        return value

    @staticmethod
    def _safe_relative(value: str) -> PurePosixPath:
        path = PurePosixPath(value)
        if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in path.parts):
            raise DownloadManagerError("Repo 檔案路徑不安全")
        if "\\" in value or "\0" in value:
            raise DownloadManagerError("Repo 檔案路徑不安全")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        for part in path.parts:
            if any(char in part for char in '<>:"|?*') or part.endswith((" ", ".")):
                raise DownloadManagerError(f"檔名無法安全保存：{part}")
            if part.split(".", 1)[0].upper() in reserved:
                raise DownloadManagerError(f"檔名是 Windows 保留名稱：{part}")
        return path

    def _assert_inside_download_root(self, path: Path) -> None:
        self._assert_inside(self.paths.downloads.resolve(), path.resolve())

    @staticmethod
    def _assert_inside(root: Path, path: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise DownloadManagerError("檔案路徑超出允許範圍") from exc

    def _remove_tree(self, target: Path) -> None:
        root = self.paths.downloads.resolve()
        self._assert_inside(root, target)
        if target == root or len(target.relative_to(root).parts) < 4:
            raise DownloadManagerError("拒絕刪除過寬的目錄")
        for path in target.rglob("*"):
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                raise DownloadManagerError("下載目錄含有 link/reparse point，拒絕自動刪除")
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise DownloadManagerError(f"無法刪除下載檔案：{exc}") from exc
