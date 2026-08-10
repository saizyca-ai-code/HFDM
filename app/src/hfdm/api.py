from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from huggingface_hub.errors import HfHubHTTPError

from .database import Database
from .download_manager import DownloadManager, DownloadManagerError
from .events import EventBroker
from .hf_service import HuggingFaceService
from .repo_ref import InvalidRepoReference
from .schemas import (
    AppSettingsView,
    CreateTaskRequest,
    IdentityView,
    RepoResolution,
    RepoResolveRequest,
    ResumeTaskRequest,
    TaskView,
)


def token_value(secret: object | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()  # type: ignore[attr-defined]
    if len(value) > 512:
        raise HTTPException(status_code=422, detail="HF token 長度不正確")
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
    manager: DownloadManager,
    broker: EventBroker,
) -> APIRouter:
    router = APIRouter(prefix="/api")

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
            return hf.resolve(payload.source, token_value(payload.hf_token))
        except InvalidRepoReference as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            code = exc.response.status_code if exc.response else 502
            detail = "無法存取 Hugging Face repo，請確認網址、權限或 token"
            raise HTTPException(status_code=code if code in {401, 403, 404} else 502, detail=detail) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"讀取 Hugging Face repo 失敗：{exc}") from exc

    @router.post("/tasks", response_model=TaskView, status_code=201)
    def create_task(payload: CreateTaskRequest) -> dict:
        try:
            token = token_value(payload.hf_token)
            resolution = hf.resolve(payload.source, token)
            return manager.create_task(resolution, payload.selected_files, token)
        except (InvalidRepoReference, DownloadManagerError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HfHubHTTPError as exc:
            raise HTTPException(status_code=403, detail="無法使用提供的 token 存取此 repo") from exc

    @router.get("/tasks", response_model=list[TaskView])
    def list_tasks() -> list[dict]:
        return db.list_tasks()

    @router.get("/tasks/{task_id}", response_model=TaskView)
    def get_task(task_id: str) -> dict:
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="找不到下載任務")
        return task

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
            return manager.resume(task_id, token_value(payload.hf_token))
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.post("/tasks/{task_id}/retry", response_model=TaskView, dependencies=[Depends(require_admin)])
    def retry_task(task_id: str, payload: ResumeTaskRequest) -> dict:
        try:
            return manager.retry(task_id, token_value(payload.hf_token))
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.post("/tasks/{task_id}/cancel", response_model=TaskView, dependencies=[Depends(require_admin)])
    def cancel_task(task_id: str) -> dict:
        try:
            return manager.cancel(task_id)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.delete("/tasks/{task_id}", status_code=204, dependencies=[Depends(require_admin)])
    def delete_task(task_id: str, delete_files: bool = True) -> None:
        try:
            manager.delete(task_id, delete_files=delete_files)
        except DownloadManagerError as exc:
            raise command_error(exc) from exc

    @router.get("/events")
    def events() -> StreamingResponse:
        return StreamingResponse(broker.stream(), media_type="text/event-stream")

    @router.get("/settings", response_model=AppSettingsView)
    def get_settings() -> dict[str, int]:
        return db.get_settings()

    @router.put("/settings", response_model=AppSettingsView, dependencies=[Depends(require_admin)])
    def update_settings(payload: AppSettingsView) -> dict[str, int]:
        db.set_settings(payload.model_dump())
        return db.get_settings()

    @router.get("/files/{task_id}/{file_path:path}")
    def download_file(task_id: str, file_path: str, request: Request) -> StreamingResponse:
        task = db.get_task(task_id)
        file = db.get_file(task_id, file_path)
        if not task or not file or file["status"] != "completed":
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
