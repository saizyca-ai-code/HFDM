export type RepoFile = {
  path: string
  size: number
  lfs: boolean
  remote_id?: string | null
  sha256?: string | null
  primary: boolean
  file_type?: string | null
  format?: string | null
  precision?: string | null
  scan_status?: string | null
  provider_metadata: Record<string, unknown>
}

export type SourceVersion = {
  id: string
  name: string
  base_model?: string | null
  created_at?: string | null
}

export type RepoResolution = {
  provider: "huggingface" | "civitai"
  repo_id: string
  repo_type: string
  requested_revision: string
  commit_hash: string
  files: RepoFile[]
  total_bytes: number
  suggested_files: string[]
  display_name?: string | null
  version_name?: string | null
  versions: SourceVersion[]
  provider_metadata: Record<string, unknown>
}

export type TaskFile = {
  id: string
  path: string
  size: number
  status: string
  downloaded_bytes: number
  error?: string | null
  local_status: "available" | "partial" | "moved" | "changed" | "unknown"
  observed_size?: number | null
  observed_mtime_ns?: number | null
  remote_id?: string | null
  expected_sha256?: string | null
  provider_metadata: Record<string, unknown>
}

export type DownloadTask = {
  id: string
  provider: string
  repo_type: string
  repo_id: string
  requested_revision: string
  commit_hash: string
  status: string
  transfer_status: string
  local_availability: "available" | "partial" | "moved" | "changed" | "unknown"
  total_bytes: number
  downloaded_bytes: number
  speed_bps: number
  eta_seconds?: number | null
  requires_token: boolean
  created_at: string
  updated_at: string
  completed_at?: string | null
  last_reconciled_at?: string | null
  error?: string | null
  display_name?: string | null
  provider_metadata: Record<string, unknown>
  files: TaskFile[]
}

export type TaskInspection = {
  resolution: RepoResolution
  selected_files: string[]
  unavailable_selected_files: string[]
  update_available: boolean
  can_update_in_place: boolean
}

export type TaskConfigurationResult = {
  task: DownloadTask
  created_new: boolean
  update_available: boolean
}

export type LibraryFile = {
  record_id: string
  id: string
  path: string
  size: number
  local_status: "available" | "partial" | "moved" | "changed" | "unknown"
  observed_size?: number | null
  provider_metadata: Record<string, unknown>
}

export type LibraryItem = {
  key: string
  provider: string
  repo_type: string
  repo_id: string
  requested_revision: string
  commit_hash: string
  destination: string
  latest_record_id: string
  latest_transfer_status: string
  local_availability: "available" | "partial" | "moved" | "changed" | "unknown"
  history_count: number
  total_bytes: number
  requires_token: boolean
  display_name?: string | null
  provider_metadata: Record<string, unknown>
  restore_record_ids: string[]
  files: LibraryFile[]
}

export type AppSettings = {
  max_concurrent_files: number
  max_storage_bytes: number
  min_free_bytes: number
  retention_days: number
  allow_delete_files: boolean
  civitai_segments: number
}

function sourceToken(source: string, token: string): Record<string, string | null> {
  const civitai = /(^\s*\d+\s*$)|(^\s*(model|version)s?[:/])|civitai\.com/i.test(source)
  return civitai ? { civitai_token: token || null } : { hf_token: token || null }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  identity: () => request<{ role: "admin" | "visitor"; is_admin: boolean }>("/api/identity"),
  resolveRepo: (source: string, token: string, includeGlobs: string[] = [], excludeGlobs: string[] = [], versionId?: number) =>
    request<RepoResolution>("/api/repos/resolve", {
      method: "POST",
      body: JSON.stringify({ source, ...sourceToken(source, token), include_globs: includeGlobs, exclude_globs: excludeGlobs, civitai_version_id: versionId || null }),
    }),
  createTask: (source: string, selectedFiles: string[], token: string, versionId?: number) =>
    request<DownloadTask>("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ source, selected_files: selectedFiles, ...sourceToken(source, token), civitai_version_id: versionId || null }),
    }),
  tasks: () => request<DownloadTask[]>("/api/tasks"),
  library: () => request<LibraryItem[]>("/api/library"),
  inspectTask: (taskId: string, provider: string, token = "", versionId?: number) =>
    request<TaskInspection>(`/api/tasks/${encodeURIComponent(taskId)}/inspect`, {
      method: "POST",
      body: JSON.stringify({ [provider === "civitai" ? "civitai_token" : "hf_token"]: token || null, civitai_version_id: versionId || null }),
    }),
  updateTaskConfiguration: (taskId: string, selectedFiles: string[], provider: string, token = "", versionId?: number) =>
    request<TaskConfigurationResult>(`/api/tasks/${encodeURIComponent(taskId)}/configuration`, {
      method: "PUT",
      body: JSON.stringify({ selected_files: selectedFiles, [provider === "civitai" ? "civitai_token" : "hf_token"]: token || null, civitai_version_id: versionId || null }),
    }),
  reconcileHistory: () => request<{ updated: number }>("/api/history/reconcile", { method: "POST" }),
  redownloadMissing: (taskId: string, provider: string, token = "") =>
    request<DownloadTask>(`/api/tasks/${encodeURIComponent(taskId)}/redownload-missing`, {
      method: "POST",
      body: JSON.stringify({ [provider === "civitai" ? "civitai_token" : "hf_token"]: token || null }),
    }),
  command: (taskId: string, command: "pause" | "resume" | "retry" | "cancel", provider: string, token = "") =>
    request<DownloadTask>(`/api/tasks/${taskId}/${command}`, {
      method: "POST",
      body: JSON.stringify({ [provider === "civitai" ? "civitai_token" : "hf_token"]: token || null }),
    }),
  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (settings: AppSettings) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
  deleteTask: (taskId: string, deleteFiles = false) =>
    fetch(`/api/tasks/${encodeURIComponent(taskId)}?delete_files=${deleteFiles}`, { method: "DELETE" }).then(async (response) => {
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.detail || "刪除失敗")
      }
    }),
  deleteTaskFiles: (taskId: string) =>
    request<DownloadTask>(`/api/tasks/${encodeURIComponent(taskId)}/files`, { method: "DELETE" }),
}

export function fileDownloadUrl(taskId: string, path: string): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/")
  return `/api/files/${encodeURIComponent(taskId)}/${encoded}`
}
