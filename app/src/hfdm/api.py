from __future__ import annotations

import mimetypes
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from huggingface_hub.errors import HfHubHTTPError

from .civitai_ref import InvalidCivitaiReference, is_civitai_reference
from .civitai_service import CivitaiService, CivitaiServiceError
from .database import Database
from .download_manager import DownloadManager, DownloadManagerError
from .events import EventBroker
from .file_selection import InvalidGlobPattern
from .hf_service import HuggingFaceService
from .repo_ref import InvalidRepoReference
from .schemas import (
    AppSettingsView,
    CreateTaskRequest,
    DashboardView,
    IdentityView,
    InspectTaskRequest,
    LibraryItemView,
    RepoResolution,
    RepoResolveRequest,
    RedownloadTaskRequest,
    ResumeTaskRequest,
    SourceDateRefreshRequest,
    SourceDateRefreshResult,
    TaskConfigurationResult,
    TaskInspection,
    TaskView,
    TimelineDateRequest,
    UpdateTaskConfigurationRequest,
    UserTagRequest,
    UserTagView,
)


def token_value(secret: object | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()  # type: ignore[attr-defined]
    if len(value) > 512:
        raise HTTPException(status_code=422, detail="Token 長度不正確")
    return value or None


def is_admin(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="此操作只允許伺服器本機管理者")


def create_router(
    db: Database,
    hf: HuggingFaceService,
    civitai: CivitaiService,
    manager: DownloadManager,
    broker: EventBroker,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def resolve_source(
        payload: RepoResolveRequest | CreateTaskRequest,
    ) -> tuple[RepoResolution, str | None]:
        if is_civitai_reference(payload.source):
            token = token_value(payload.civitai_token)
            return civitai.resolve(payload.source, token, payload.civitai_version_id), token
        token = token_value(payload.hf_token)
        include_globs = payload.include_globs if isinstance(payload, RepoResolveRequest) else None
        exclude_globs = payload.exclude_globs if isinstance(payload, RepoResolveRequest) else None
        return hf.resolve(payload.source, token, include_globs, exclude_globs), token

    def task_token(task: dict, payload: object) -> str | None:
        field = "civitai_token" if task["provider"] == "civitai" else "hf_token"
        return token_value(getattr(payload, field, None))

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/identity", response_model=IdentityView)
    def identity(request: Request) -> IdentityView:
        admin = is_admin(request)
        return IdentityView(role="admin" if admin else "visitor", is_admin=admin)

    @router.post("/repos/resolve", response_model=RepoResolution)
    def resolve_repo(payload: RepoResolveRequest) -> RepoResolution:
        try:
            return resolve_source(payload)[0]
        except (InvalidRepoReference, InvalidCivitaiReference, InvalidGlobPattern) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CivitaiServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            code = exc.response.status_code if exc.response else 502
            detail = "無法存取 Hugging Face repo，請確認網址、權限或 token"
            raise HTTPException(status_code=code if code in {401, 403, 404} else 502, detail=detail) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"讀取 Hugging Face repo 失敗：{exc}") from exc

    @router.post("/tasks", response_model=TaskView, status_code=201)
    def create_task(payload: CreateTaskRequest) -> dict:
        try:
            resolution, token = resolve_source(payload)
            return manager.create_task(resolution, payload.selected_files, token)
        except (InvalidRepoReference, InvalidCivitaiReference, DownloadManagerError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except CivitaiServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            raise HTTPException(status_code=403, detail="無法使用提供的 token 存取此 repo") from exc

    @router.get("/tasks", response_model=list[TaskView])
    def list_tasks() -> list[dict]:
        return db.list_tasks()

    @router.get("/library", response_model=list[LibraryItemView])
    def list_library() -> list[dict]:
        return db.list_library_items()

    @router.put(
        "/library/{record_id}/timeline-date",
        dependencies=[Depends(require_admin)],
    )
    def update_library_timeline_date(record_id: str, payload: TimelineDateRequest) -> dict[str, Any]:
        try:
            db.set_library_timeline_date(
                record_id,
                payload.timeline_date.isoformat() if payload.timeline_date else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"updated": True}

    @router.post(
        "/library/{record_id}/refresh-source-date",
        response_model=SourceDateRefreshResult,
        dependencies=[Depends(require_admin)],
    )
    def refresh_library_source_date(
        record_id: str,
        payload: SourceDateRefreshRequest,
    ) -> dict[str, Any]:
        task = db.get_task(record_id)
        if not task:
            raise HTTPException(status_code=404, detail="找不到內容庫項目")
        token = task_token(task, payload)
        try:
            if task["provider"] == "civitai":
                created_at, updated_at = civitai.source_dates(
                    task["repo_id"],
                    task["commit_hash"],
                    token,
                )
            else:
                created_at, updated_at = hf.source_dates(
                    task["repo_id"],
                    task["commit_hash"],
                    token,
                    task["repo_type"],
                )
            if not created_at:
                raise HTTPException(status_code=422, detail="模型來源沒有提供 createdAt")
            return db.set_library_source_dates(
                record_id,
                created_at,
                updated_at,
                apply_source_date=payload.apply_source_date,
            )
        except CivitaiServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            code = exc.response.status_code if exc.response else 502
            raise HTTPException(
                status_code=code if code in {401, 403, 404} else 502,
                detail="無法取得 Hugging Face createdAt，請確認 repo、權限或 token",
            ) from exc
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            provider = "Civitai" if task["provider"] == "civitai" else "Hugging Face"
            raise HTTPException(status_code=502, detail=f"取得 {provider} createdAt 失敗：{exc}") from exc

    @router.get("/dashboard", response_model=DashboardView)
    def dashboard(days: int = Query(default=90, ge=0, le=3650)) -> dict[str, Any]:
        return db.dashboard(days)

    @router.post(
        "/library/{record_id}/archive",
        dependencies=[Depends(require_admin)],
    )
    def archive_library_item(record_id: str) -> dict[str, Any]:
        try:
            return manager.archive_library_item(record_id)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.get("/user-tags", response_model=list[UserTagView])
    def list_user_tags() -> list[dict[str, Any]]:
        return db.list_user_tags()

    @router.post(
        "/user-tags",
        response_model=UserTagView,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    def create_user_tag(payload: UserTagRequest) -> dict[str, Any]:
        try:
            return db.create_user_tag(str(uuid.uuid4()), payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.put(
        "/user-tags/{tag_id}",
        response_model=UserTagView,
        dependencies=[Depends(require_admin)],
    )
    def rename_user_tag(tag_id: str, payload: UserTagRequest) -> dict[str, Any]:
        try:
            return db.rename_user_tag(tag_id, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=404 if "找不到" in str(exc) else 422, detail=str(exc)) from exc

    @router.delete(
        "/user-tags/{tag_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    def delete_user_tag(tag_id: str) -> None:
        try:
            db.delete_user_tag(tag_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/library/{record_id}/user-tags/{tag_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    def add_library_user_tag(record_id: str, tag_id: str) -> None:
        try:
            db.add_user_tag_to_record(record_id, tag_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete(
        "/library/{record_id}/user-tags/{tag_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    def remove_library_user_tag(record_id: str, tag_id: str) -> None:
        try:
            db.remove_user_tag_from_record(record_id, tag_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/library/{record_id}/open-folder",
        dependencies=[Depends(require_admin)],
    )
    def open_library_folder(record_id: str, scope: str = "version") -> dict[str, bool]:
        try:
            manager.open_task_folder(record_id, scope)
            return {"opened": True}
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.get("/tasks/{task_id}", response_model=TaskView)
    def get_task(task_id: str) -> dict:
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="找不到下載任務")
        return task

    @router.post(
        "/tasks/{task_id}/inspect",
        response_model=TaskInspection,
        dependencies=[Depends(require_admin)],
    )
    def inspect_task(task_id: str, payload: InspectTaskRequest) -> dict:
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="找不到下載任務")
        try:
            token = task_token(task, payload)
            if task["provider"] == "civitai":
                resolution = civitai.resolve_existing(
                    task["repo_id"],
                    task["requested_revision"],
                    token,
                    payload.civitai_version_id,
                )
            else:
                resolution = hf.resolve_existing(
                    task["repo_id"],
                    task["requested_revision"],
                    token,
                    repo_type=task["repo_type"],
                )
        except CivitaiServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            raise HTTPException(status_code=403, detail="無法使用提供的 token 存取此 repo") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"重新檢查 Hugging Face repo 失敗：{exc}") from exc
        update_available = resolution.commit_hash != task["commit_hash"]
        available_paths = {file.path for file in resolution.files}
        selected_files = [file["path"] for file in task["files"]]
        return {
            "resolution": resolution,
            "selected_files": selected_files,
            "unavailable_selected_files": [
                path for path in selected_files if path not in available_paths
            ],
            "update_available": update_available,
            "can_update_in_place": (
                not update_available
                and task["status"] in {"queued", "paused", "auth_required"}
            ),
        }

    @router.put(
        "/tasks/{task_id}/configuration",
        response_model=TaskConfigurationResult,
        dependencies=[Depends(require_admin)],
    )
    def update_task_configuration(
        task_id: str,
        payload: UpdateTaskConfigurationRequest,
    ) -> dict:
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="找不到下載任務")
        try:
            token = task_token(task, payload)
            if task["provider"] == "civitai":
                resolution = civitai.resolve_existing(
                    task["repo_id"],
                    task["requested_revision"],
                    token,
                    payload.civitai_version_id,
                )
            else:
                resolution = hf.resolve_existing(
                    task["repo_id"],
                    task["requested_revision"],
                    token,
                    repo_type=task["repo_type"],
                )
            update_available = resolution.commit_hash != task["commit_hash"]
            configured, created_new = manager.reconfigure_task(
                task_id,
                resolution,
                payload.selected_files,
                token,
            )
            return {
                "task": configured,
                "created_new": created_new,
                "update_available": update_available,
            }
        except DownloadManagerError as exc:
            raise command_error(exc) from exc
        except CivitaiServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            raise HTTPException(status_code=403, detail="無法使用提供的 token 存取此 repo") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"更新下載任務失敗：{exc}") from exc

    @router.post("/history/reconcile", dependencies=[Depends(require_admin)])
    def reconcile_history() -> dict[str, int]:
        return {"updated": manager.reconcile()}

    def command_error(exc: DownloadManagerError) -> HTTPException:
        return HTTPException(status_code=409, detail=str(exc))

    @router.post("/tasks/{task_id}/pause", response_model=TaskView, dependencies=[Depends(require_admin)])
    def pause_task(task_id: str) -> dict:
        try:
            return manager.pause(task_id)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.post("/tasks/{task_id}/resume", response_model=TaskView, dependencies=[Depends(require_admin)])
    def resume_task(task_id: str, payload: ResumeTaskRequest) -> dict:
        try:
            task = db.get_task(task_id)
            if not task:
                raise DownloadManagerError("找不到下載任務")
            return manager.resume(task_id, task_token(task, payload))
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.post("/tasks/{task_id}/retry", response_model=TaskView, dependencies=[Depends(require_admin)])
    def retry_task(task_id: str, payload: ResumeTaskRequest) -> dict:
        try:
            task = db.get_task(task_id)
            if not task:
                raise DownloadManagerError("找不到下載任務")
            return manager.retry(task_id, task_token(task, payload))
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.post(
        "/tasks/{task_id}/redownload-missing",
        response_model=TaskView,
        dependencies=[Depends(require_admin)],
    )
    def redownload_missing(task_id: str, payload: RedownloadTaskRequest) -> dict:
        try:
            task = db.get_task(task_id)
            if not task:
                raise DownloadManagerError("找不到下載任務")
            return manager.redownload_missing(task_id, task_token(task, payload))
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.post("/tasks/{task_id}/cancel", response_model=TaskView, dependencies=[Depends(require_admin)])
    def cancel_task(task_id: str) -> dict:
        try:
            return manager.cancel(task_id)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.delete("/tasks/{task_id}", status_code=204, dependencies=[Depends(require_admin)])
    def delete_task(task_id: str, delete_files: bool = False) -> None:
        try:
            manager.delete(task_id, delete_files=delete_files)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.delete(
        "/tasks/{task_id}/files",
        response_model=TaskView,
        dependencies=[Depends(require_admin)],
    )
    def delete_task_files(task_id: str) -> dict:
        try:
            return manager.delete_files(task_id)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.get("/events")
    def events() -> StreamingResponse:
        return StreamingResponse(broker.stream(), media_type="text/event-stream")

    @router.get("/settings", response_model=AppSettingsView)
    def get_settings() -> dict[str, Any]:
        return db.get_settings()

    @router.put("/settings", response_model=AppSettingsView, dependencies=[Depends(require_admin)])
    def update_settings(payload: AppSettingsView) -> dict[str, Any]:
        db.set_settings(payload.model_dump())
        return db.get_settings()

    @router.get("/files/{task_id}/{file_path:path}")
    def download_file(task_id: str, file_path: str, request: Request) -> StreamingResponse:
        task = db.get_task(task_id)
        file = db.get_file(task_id, file_path)
        if (
            not task
            or not file
            or file["status"] != "completed"
            or file["local_status"] != "available"
        ):
            raise HTTPException(status_code=404, detail="檔案尚未完成或不存在")
        root = manager.task_destination(task)
        target = (root / Path(file_path)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="檔案路徑不安全") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="找不到檔案")
        return _range_response(target, request.headers.get("range"))

    return router


def _range_response(path: Path, range_header: str | None) -> StreamingResponse:
    size = path.stat().st_size
    start, end = 0, size - 1
    response_status = 200
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'attachment; filename="{path.name}"',
        "ETag": f'"{size:x}-{path.stat().st_mtime_ns:x}"',
    }
    if range_header:
        try:
            unit, value = range_header.split("=", 1)
            if unit.strip().lower() != "bytes" or "," in value:
                raise ValueError
            left, right = value.split("-", 1)
            if left:
                start = int(left)
                end = int(right) if right else size - 1
            else:
                suffix = int(right)
                start = max(0, size - suffix)
                end = size - 1
            if start < 0 or end < start or start >= size:
                raise ValueError
            end = min(end, size - 1)
            response_status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        except ValueError as exc:
            raise HTTPException(
                status_code=416,
                detail="Range 無效",
                headers={"Content-Range": f"bytes */{size}"},
            ) from exc
    length = end - start + 1
    headers["Content-Length"] = str(length)

    def chunks() -> Iterator[bytes]:
        remaining = length
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return StreamingResponse(chunks(), status_code=response_status, headers=headers, media_type=media_type)
