from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, SecretStr


TaskStatus = Literal[
    "queued",
    "resolving",
    "downloading",
    "pausing",
    "paused",
    "auth_required",
    "completed",
    "partial",
    "failed",
    "cancelled",
]

LocalAvailability = Literal["available", "partial", "moved", "changed", "unknown"]
RepoType = Literal["model", "dataset"]


class RepoResolveRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    hf_token: SecretStr | None = None
    include_globs: list[str] = Field(default_factory=list, max_length=32)
    exclude_globs: list[str] = Field(default_factory=list, max_length=32)


class RepoFileInfo(BaseModel):
    path: str
    size: int = 0
    lfs: bool = False


class RepoResolution(BaseModel):
    repo_id: str
    repo_type: RepoType = "model"
    requested_revision: str
    commit_hash: str
    files: list[RepoFileInfo]
    total_bytes: int
    suggested_files: list[str] = Field(default_factory=list)


class CreateTaskRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    selected_files: list[str] = Field(min_length=1)
    hf_token: SecretStr | None = None


class ResumeTaskRequest(BaseModel):
    hf_token: SecretStr | None = None


class RedownloadTaskRequest(BaseModel):
    hf_token: SecretStr | None = None


class InspectTaskRequest(BaseModel):
    hf_token: SecretStr | None = None


class UpdateTaskConfigurationRequest(BaseModel):
    selected_files: list[str] = Field(min_length=1)
    hf_token: SecretStr | None = None


class TaskFileView(BaseModel):
    id: str
    path: str
    size: int
    status: str
    downloaded_bytes: int
    error: str | None = None
    local_status: LocalAvailability = "unknown"
    observed_size: int | None = None
    observed_mtime_ns: int | None = None


class TaskView(BaseModel):
    id: str
    provider: str = "huggingface"
    repo_type: RepoType = "model"
    repo_id: str
    requested_revision: str
    commit_hash: str
    status: TaskStatus
    transfer_status: str
    local_availability: LocalAvailability = "unknown"
    total_bytes: int
    downloaded_bytes: int
    speed_bps: float = 0
    eta_seconds: int | None = None
    requires_token: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    last_reconciled_at: datetime | None = None
    error: str | None = None
    files: list[TaskFileView] = Field(default_factory=list)


class TaskInspection(BaseModel):
    resolution: RepoResolution
    selected_files: list[str]
    unavailable_selected_files: list[str] = Field(default_factory=list)
    update_available: bool
    can_update_in_place: bool


class TaskConfigurationResult(BaseModel):
    task: TaskView
    created_new: bool
    update_available: bool


class LibraryFileView(BaseModel):
    record_id: str
    id: str
    path: str
    size: int
    local_status: LocalAvailability
    observed_size: int | None = None


class LibraryItemView(BaseModel):
    key: str
    provider: str
    repo_type: str
    repo_id: str
    requested_revision: str
    commit_hash: str
    destination: str
    latest_record_id: str
    latest_transfer_status: str
    local_availability: LocalAvailability
    history_count: int
    total_bytes: int
    requires_token: bool
    restore_record_ids: list[str] = Field(default_factory=list)
    files: list[LibraryFileView] = Field(default_factory=list)


class AppSettingsView(BaseModel):
    max_concurrent_files: int = Field(ge=1, le=16)
    max_storage_bytes: int = Field(ge=0)
    min_free_bytes: int = Field(ge=0)
    retention_days: int = Field(ge=0)
    allow_delete_files: bool = True


class IdentityView(BaseModel):
    role: Literal["admin", "visitor"]
    is_admin: bool
