from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from .config import AppPaths
from .database import Database, utc_now


class DownloadReconciler:
    def __init__(self, paths: AppPaths, db: Database):
        self.paths = paths
        self.db = db

    def run(self, record_id: str | None = None, *, allow_hash: bool = True) -> int:
        updated = 0
        for record in self.db.list_reconciliation_records(record_id):
            if record["transfer_status"] not in {"completed", "failed", "cancelled"}:
                continue
            completed_files = [
                file for file in record["files"] if file["transfer_status"] == "completed"
            ]
            if not completed_files:
                continue
            reconciled_at = utc_now()
            try:
                destination = self._safe_path(record["destination"])
            except ValueError:
                self.db.apply_reconciliation(record["id"], "unknown", (), reconciled_at)
                updated += 1
                continue

            if record.get("archived_at") and not destination.exists():
                observations = [
                    self._archived_observation(record["id"], file, reconciled_at)
                    for file in completed_files
                ]
            else:
                observations = [
                    self._observe(record["id"], destination, file, reconciled_at, allow_hash)
                    for file in completed_files
                ]
            statuses = [item["local_status"] for item in observations]
            if "changed" in statuses:
                availability = "changed"
            elif "unknown" in statuses:
                availability = "unknown"
            elif all(status == "available" for status in statuses):
                availability = "available"
            elif all(status == "archived" for status in statuses):
                availability = "archived"
            elif all(status == "moved" for status in statuses):
                availability = "moved"
            else:
                availability = "partial"
            self.db.apply_reconciliation(record["id"], availability, observations, reconciled_at)
            updated += 1
        return updated

    @staticmethod
    def _archived_observation(
        record_id: str,
        file: dict[str, Any],
        reconciled_at: str,
    ) -> dict[str, Any]:
        return {
            "id": file["id"],
            "record_id": record_id,
            "local_status": "archived",
            "observed_size": None,
            "observed_mtime_ns": None,
            "observed_sha256": None,
            "last_reconciled_at": reconciled_at,
        }

    def _safe_path(self, relative_text: str) -> Path:
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("unsafe download path")
        root = self.paths.downloads.resolve()
        target = (root / Path(*relative.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("download path escaped its root") from exc
        return target

    def _observe(
        self,
        record_id: str,
        destination: Path,
        file: dict[str, Any],
        reconciled_at: str,
        allow_hash: bool,
    ) -> dict[str, Any]:
        observation: dict[str, Any] = {
            "id": file["id"],
            "record_id": record_id,
            "local_status": "moved",
            "observed_size": None,
            "observed_mtime_ns": None,
            "observed_sha256": None,
            "last_reconciled_at": reconciled_at,
        }
        relative = PurePosixPath(file["path"])
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            observation["local_status"] = "changed"
            return observation
        root = self.paths.downloads.resolve()
        target = (destination / Path(*relative.parts)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            observation["local_status"] = "changed"
            return observation
        if not target.is_file():
            return observation

        stat = target.stat()
        observation["observed_size"] = stat.st_size
        observation["observed_mtime_ns"] = stat.st_mtime_ns
        if int(file["expected_size"]) > 0 and stat.st_size != int(file["expected_size"]):
            observation["local_status"] = "changed"
            return observation

        expected_sha256 = file.get("expected_sha256")
        if expected_sha256:
            cached = (
                file.get("observed_size") == stat.st_size
                and file.get("observed_mtime_ns") == stat.st_mtime_ns
                and file.get("observed_sha256")
            )
            digest = str(file["observed_sha256"]) if cached else None
            if digest is None and allow_hash:
                digest = self._sha256(target)
            observation["observed_sha256"] = digest
            if digest is None:
                observation["local_status"] = "unknown"
                return observation
            if digest.casefold() != str(expected_sha256).casefold():
                observation["local_status"] = "changed"
                return observation

        observation["local_status"] = "available"
        return observation

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
