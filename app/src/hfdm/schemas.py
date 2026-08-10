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


class RepoResolveRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    hf_token: SecretStr | None = None


class RepoFileInfo(BaseModel):
    path: str
    size: int = 0
    lfs: bool = False


class RepoResolution(BaseModel):
    repo_id: str
    repo_type: str = "model"
    requested_revision: str
    commit_hash: str
    files: list[RepoFileInfo]
    total_bytes: int


class CreateTaskRequest(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    selected_files: list[str] = Field(min_length=1)
    hf_token: SecretStr | None = None


class ResumeTaskRequest(BaseModel):
    hf_token: SecretStr | None = None


class TaskFileView(BaseModel):
    id: str
    path: str
    size: int
    status: str
    downloaded_bytes: int
    error: str | None = None


class TaskView(BaseModel):
    id: str
    repo_id: str
    requested_revision: str
    commit_hash: str
    status: TaskStatus
    total_bytes: int
    downloaded_bytes: int
    speed_bps: float = 0
    eta_seconds: int | None = None
    requires_token: bool
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    files: list[TaskFileView] = Field(default_factory=list)


class AppSettingsView(BaseModel):
    max_concurrent_files: int = Field(ge=1, le=16)
    max_storage_bytes: int = Field(ge=0)
    min_free_bytes: int = Field(ge=0)
    retention_days: int = Field(ge=0)
    allow_delete_files: bool = True


class IdentityView(BaseModel):
    role: Literal["admin", "visitor"]
    is_admin: bool
