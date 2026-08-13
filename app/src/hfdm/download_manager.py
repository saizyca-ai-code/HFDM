from __future__ import annotations

import json
import hashlib
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
from .reconciliation import DownloadReconciler
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
    def __init__(
        self,
        paths: AppPaths,
        db: Database,
        broker: EventBroker,
        *,
        reconciliation_interval: float = 30.0,
    ):
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
        self._last_reconciliation = 0.0
        self._reconciliation_interval = reconciliation_interval
        self._reconciler = DownloadReconciler(paths, db)
        self._normalize_task_destinations()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.db.recover_interrupted()
        self.reconcile()
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
        *,
        deduplicate: bool = True,
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

        if deduplicate:
            matches = [
                task
                for task in self.db.list_tasks()
                if task["provider"] == resolution.provider
                and task["repo_type"] == resolution.repo_type
                and task["repo_id"] == resolution.repo_id
                and task["commit_hash"] == resolution.commit_hash
            ]
            if matches:
                requested = {item.path for item in selected}
                existing = {
                    file["path"]
                    for task in matches
                    for file in task["files"]
                    if file["path"] in available
                }
                union = requested | existing
                if len(matches) == 1 and union == existing:
                    if token:
                        self._tokens[matches[0]["id"]] = token
                    return matches[0]
                if any(task["status"] in {"downloading", "pausing"} for task in matches):
                    raise DownloadManagerError("相同來源版本已有執行中的任務；請先暫停後再合併檔案")
                consolidated = self.create_task(
                    resolution,
                    sorted(union),
                    token,
                    deduplicate=False,
                )
                for task in matches:
                    self.db.delete_task(task["id"])
                    self._tokens.pop(task["id"], None)
                    self._publish(task["id"], "merged")
                return consolidated

        destination_key = self._destination_key(
            resolution.provider,
            resolution.repo_type,
            resolution.repo_id,
            resolution.commit_hash,
        )
        destination = self._resolve_destination_key(destination_key)
        missing_bytes = sum(
            item.size for item in selected if not (destination / Path(item.path)).is_file()
        )
        self._assert_capacity(missing_bytes)

        now = utc_now()
        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "provider": resolution.provider,
            "repo_id": resolution.repo_id,
            "repo_type": resolution.repo_type,
            "requested_revision": resolution.requested_revision,
            "commit_hash": resolution.commit_hash,
            "destination": destination_key,
            "status": "queued",
            "total_bytes": sum(item.size for item in selected),
            "requires_token": int(
                bool(token) or bool(resolution.provider_metadata.get("requires_token"))
            ),
            "display_name": resolution.display_name,
            "provider_metadata": resolution.provider_metadata,
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
                "remote_id": item.remote_id,
                "expected_sha256": item.sha256,
                "download_url": item.provider_metadata.get("download_url"),
                "provider_metadata": item.provider_metadata,
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

    def reconfigure_task(
        self,
        task_id: str,
        resolution: RepoResolution,
        selected_paths: list[str],
        token: str | None,
    ) -> tuple[dict[str, Any], bool]:
        task = self._require_task(task_id)
        if task["status"] in {"downloading", "pausing"}:
            raise DownloadManagerError("Pause the active task before changing its file selection")
        selected = self._selected_repo_files(resolution, selected_paths)
        update_available = task["commit_hash"] != resolution.commit_hash
        editable = task["status"] in {"queued", "paused", "auth_required"}
        if update_available or not editable:
            created = self.create_task(
                resolution,
                [item.path for item in selected],
                token,
                deduplicate=False,
            )
            self.db.delete_task(task_id)
            self._tokens.pop(task_id, None)
            self._publish(task_id, "replaced")
            return created, True

        destination = self.task_destination(task)
        missing_bytes = sum(
            item.size for item in selected if not (destination / Path(item.path)).is_file()
        )
        self._assert_capacity(missing_bytes)
        current = {file["path"]: file for file in task["files"]}
        default_status = "paused" if task["status"] in {"paused", "auth_required"} else "queued"
        file_rows: list[dict[str, Any]] = []
        for item in selected:
            existing = current.get(item.path)
            unchanged = (
                existing is not None
                and int(existing["size"]) == item.size
                and existing.get("remote_id") == item.remote_id
                and existing.get("expected_sha256") == item.sha256
            )
            file_rows.append(
                {
                    "id": existing["id"] if unchanged else str(uuid.uuid4()),
                    "task_id": task_id,
                    "path": item.path,
                    "size": item.size,
                    "status": existing["status"] if unchanged else default_status,
                    "downloaded_bytes": existing["downloaded_bytes"] if unchanged else 0,
                    "error": existing.get("error") if unchanged else None,
                    "local_status": existing.get("local_status", "unknown") if unchanged else "unknown",
                    "observed_size": existing.get("observed_size") if unchanged else None,
                    "observed_mtime_ns": existing.get("observed_mtime_ns") if unchanged else None,
                    "observed_sha256": existing.get("observed_sha256") if unchanged else None,
                    "last_reconciled_at": existing.get("last_reconciled_at") if unchanged else None,
                    "remote_id": item.remote_id,
                    "expected_sha256": item.sha256,
                    "download_url": item.provider_metadata.get("download_url"),
                    "provider_metadata": item.provider_metadata,
                }
            )
        try:
            self.db.replace_editable_task_files(
                task_id,
                file_rows,
                requires_token=True if token else None,
            )
        except ValueError as exc:
            raise DownloadManagerError(str(exc)) from exc
        if token:
            self._tokens[task_id] = token
            if task["status"] == "auth_required":
                self.db.bulk_file_status(task_id, ["paused"], "queued")
                self.db.update_task(task_id, status="queued", error=None)
        self._publish(task_id, "configuration_updated")
        self._wake.set()
        return self._require_task(task_id), False

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
            provider = "Civitai" if task.get("provider") == "civitai" else "Hugging Face"
            raise DownloadManagerError(f"請重新提供 {provider} token")
        self.db.bulk_file_status(task_id, ["paused", "failed"], "queued")
        self.db.update_task(task_id, status="queued", error=None)
        self._publish(task_id, "resumed")
        self._wake.set()
        return self._require_task(task_id)

    def retry(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task["status"] == "failed":
            self.db.begin_retry_attempt(task_id)
        return self.resume(task_id, token=token)

    def redownload_missing(self, task_id: str, token: str | None = None) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task["requires_token"] and not token:
            provider = "Civitai" if task.get("provider") == "civitai" else "Hugging Face"
            raise DownloadManagerError(f"A {provider} token is required to restore these files")
        if token:
            self._tokens[task_id] = token
        try:
            restored_paths = self.db.prepare_missing_redownload(task_id)
        except ValueError as exc:
            raise DownloadManagerError(str(exc)) from exc
        self._publish(task_id, "redownload_queued")
        self.broker.publish(
            {"type": "redownload_queued", "task_id": task_id, "files": restored_paths}
        )
        self._wake.set()
        return self._require_task(task_id)

    def reconcile(self, task_id: str | None = None) -> int:
        with self._lock:
            allow_hash = not self._active
        updated = self._reconciler.run(task_id, allow_hash=allow_hash)
        self._last_reconciliation = time.monotonic()
        if updated:
            self.broker.publish({"type": "reconciled", "task_id": task_id, "updated": updated})
        return updated

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

    def delete_files(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        with self._lock:
            if any(worker.task_id == task_id for worker in self._active.values()):
                raise DownloadManagerError("Cannot remove files while a transfer is active")
        if not self.db.get_settings()["allow_delete_files"]:
            raise DownloadManagerError("Physical file deletion is disabled")
        if self.db.destination_reference_count(task["destination"], task_id) > 0:
            raise DownloadManagerError("The destination is referenced by another history record")
        destination = self.task_destination(task)
        if destination.exists():
            self._remove_tree(destination)
        self.reconcile(task_id)
        self._publish(task_id, "files_deleted")
        return self._require_task(task_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._dispatch()
                if time.monotonic() - self._last_cleanup >= 60:
                    self._cleanup_expired()
                    self._last_cleanup = time.monotonic()
                if time.monotonic() - self._last_reconciliation >= self._reconciliation_interval:
                    self.reconcile()
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
                task = self._require_task(task_id)
                destination = self.task_destination(task)
                if (
                    destination.exists()
                    and self.db.destination_reference_count(task["destination"], task_id) == 0
                ):
                    self._remove_tree(destination)
                self.reconcile(task_id)
                self._publish(task_id, "files_expired")
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
            if self._can_reuse_file(target, file):
                self.db.update_file(
                    file["id"], status="completed", downloaded_bytes=file["size"], error=None
                )
                self.db.recompute_progress(task_id)
                self._publish(task_id, "file_reused", file["id"])
                self._wake.set()
                continue

            key = (
                f"{task['provider']}:{task['repo_type']}:{task['repo_id']}:"
                f"{task['commit_hash']}:{file['path']}"
            )
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
            "provider": task["provider"],
            "repo_id": task["repo_id"],
            "repo_type": task["repo_type"],
            "commit_hash": task["commit_hash"],
            "filename": file["path"],
            "destination": str(self.task_destination(task)),
            "token": self._tokens.get(task["id"]),
        }
        if task["provider"] == "civitai":
            payload.update(
                {
                    "expected_size": file["size"],
                    "expected_sha256": file.get("expected_sha256"),
                    "download_url": file.get("download_url"),
                    "provider_metadata": file.get("provider_metadata", {}),
                    "segments": self.db.get_settings()["civitai_segments"],
                }
            )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        return ActiveWorker(task["id"], file["id"], key, process)

    def _monitor(self, worker: ActiveWorker, expected_size: int) -> None:
        last_error: str | None = None
        auth_required = False
        current_expected_size = expected_size
        assert worker.process.stdout is not None
        for line in worker.process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "progress":
                reported_total = int(event.get("total", 0))
                if current_expected_size <= 0 and reported_total > 0:
                    current_expected_size = reported_total
                    self.db.update_file_size(worker.file_id, reported_total)
                downloaded = min(int(event.get("downloaded", 0)), current_expected_size)
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
                auth_required = event.get("kind") == "CivitaiAuthRequired"

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
                downloaded_bytes=current_expected_size,
                error=None,
            )
        elif auth_required:
            self.db.update_file(worker.file_id, status="paused", error=last_error)
            self.db.update_task(
                worker.task_id,
                status="auth_required",
                requires_token=1,
                error="請提供 Civitai API Token 後繼續",
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
        if not auth_required:
            self._finalize_task(worker.task_id)
        else:
            self._publish(worker.task_id, "auth_required", worker.file_id)
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
        if status in {"completed", "failed"}:
            self.reconcile(task_id)
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

    def _selected_repo_files(
        self,
        resolution: RepoResolution,
        selected_paths: list[str],
    ) -> list[Any]:
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
        return selected

    def task_destination(self, task: dict[str, Any]) -> Path:
        return self._resolve_destination_key(task["destination"])

    def open_task_folder(self, task_id: str, scope: str = "version") -> None:
        task = self.db.get_task(task_id)
        if not task:
            raise DownloadManagerError("找不到下載紀錄")
        destination = self.task_destination(task)
        if scope == "version":
            target = destination
        elif scope == "source":
            target = destination.parent
        else:
            raise DownloadManagerError("不支援的資料夾範圍")
        self._assert_inside_download_root(target)
        if not target.is_dir():
            raise DownloadManagerError("本機資料夾不存在，請先重新掃描內容庫")
        if sys.platform != "win32":
            raise DownloadManagerError("開啟本機資料夾目前只支援 Windows")
        try:
            subprocess.Popen(["explorer.exe", str(target)], shell=False)
        except OSError as exc:
            raise DownloadManagerError("無法開啟 Windows Explorer") from exc

    def _normalize_task_destinations(self) -> None:
        for task in self.db.list_task_locations():
            destination = self._destination_key(
                task["provider"],
                task["repo_type"],
                task["repo_id"],
                task["commit_hash"],
            )
            if task["destination"] != destination:
                self.db.set_task_destination(task["id"], destination)

    def _destination_key(
        self,
        provider: str,
        repo_type: str,
        repo_id: str,
        commit_hash: str,
    ) -> str:
        owner, repo = repo_id.split("/", 1)
        if provider == "civitai":
            if repo_type != "model" or owner != "models":
                raise DownloadManagerError("不支援的 Civitai identity")
            return PurePosixPath(
                "civitai",
                "models",
                self._safe_segment(repo),
                self._safe_segment(commit_hash),
            ).as_posix()
        if provider != "huggingface":
            raise DownloadManagerError(f"不支援的下載來源：{provider}")
        if repo_type not in {"model", "dataset"}:
            raise DownloadManagerError(f"不支援的 Hugging Face repo type：{repo_type}")
        return PurePosixPath(
            "datasets" if repo_type == "dataset" else "models",
            self._safe_segment(owner),
            self._safe_segment(repo),
            self._safe_segment(commit_hash),
        ).as_posix()

    @staticmethod
    def _can_reuse_file(target: Path, file: dict[str, Any]) -> bool:
        if not target.is_file() or target.stat().st_size != int(file["size"]):
            return False
        expected = file.get("expected_sha256")
        if not expected:
            return True
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest().casefold() == str(expected).casefold()

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
