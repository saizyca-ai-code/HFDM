from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
Provider = Literal["huggingface", "civitai"]


class RepoResolveRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    hf_token: SecretStr | None = None
    civitai_token: SecretStr | None = None
    civitai_version_id: int | None = Field(default=None, ge=1)
    include_globs: list[str] = Field(default_factory=list, max_length=32)
    exclude_globs: list[str] = Field(default_factory=list, max_length=32)


class RepoFileInfo(BaseModel):
    path: str
    size: int = 0
    lfs: bool = False
    remote_id: str | None = None
    sha256: str | None = None
    primary: bool = False
    file_type: str | None = None
    format: str | None = None
    precision: str | None = None
    scan_status: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class SourceVersionInfo(BaseModel):
    id: str
    name: str
    base_model: str | None = None
    created_at: datetime | None = None


class CivitaiSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    tag: str | None = Field(default=None, max_length=100)
    username: str | None = Field(default=None, max_length=100)
    types: list[str] = Field(default_factory=list, max_length=16)
    base_models: list[str] = Field(default_factory=list, max_length=16)
    sort: Literal["Highest Rated", "Most Downloaded", "Newest"] = "Most Downloaded"
    period: Literal["AllTime", "Year", "Month", "Week", "Day"] = "AllTime"
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=50)
    civitai_token: SecretStr | None = None


class CivitaiSearchItem(BaseModel):
    id: int
    name: str
    model_type: str | None = None
    creator: str | None = None
    latest_version_id: int | None = None
    base_model: str | None = None
    preview_url: str | None = None


class CivitaiSearchResult(BaseModel):
    items: list[CivitaiSearchItem]
    current_page: int = 1
    total_pages: int = 1
    next_page: int | None = None


class RepoResolution(BaseModel):
    provider: Provider = "huggingface"
    repo_id: str
    repo_type: RepoType = "model"
    requested_revision: str
    commit_hash: str
    files: list[RepoFileInfo]
    total_bytes: int
    suggested_files: list[str] = Field(default_factory=list)
    display_name: str | None = None
    version_name: str | None = None
    versions: list[SourceVersionInfo] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class CreateTaskRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    selected_files: list[str] = Field(min_length=1)
    hf_token: SecretStr | None = None
    civitai_token: SecretStr | None = None
    civitai_version_id: int | None = Field(default=None, ge=1)


class ResumeTaskRequest(BaseModel):
    hf_token: SecretStr | None = None
    civitai_token: SecretStr | None = None


class RedownloadTaskRequest(BaseModel):
    hf_token: SecretStr | None = None
    civitai_token: SecretStr | None = None


class InspectTaskRequest(BaseModel):
    hf_token: SecretStr | None = None
    civitai_token: SecretStr | None = None
    civitai_version_id: int | None = Field(default=None, ge=1)


class UpdateTaskConfigurationRequest(BaseModel):
    selected_files: list[str] = Field(min_length=1)
    hf_token: SecretStr | None = None
    civitai_token: SecretStr | None = None
    civitai_version_id: int | None = Field(default=None, ge=1)


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
    remote_id: str | None = None
    expected_sha256: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


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
    display_name: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
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
    display_name: str | None = None
    restore_record_ids: list[str] = Field(default_factory=list)
    files: list[LibraryFileView] = Field(default_factory=list)


class AppSettingsView(BaseModel):
    max_concurrent_files: int = Field(ge=1, le=16)
    max_storage_bytes: int = Field(ge=0)
    min_free_bytes: int = Field(ge=0)
    retention_days: int = Field(ge=0)
    allow_delete_files: bool = True
    civitai_segments: int = Field(default=4, ge=1, le=8)


class IdentityView(BaseModel):
    role: Literal["admin", "visitor"]
    is_admin: bool
