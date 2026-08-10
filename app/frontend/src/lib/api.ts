export type RepoFile = { path: string; size: number; lfs: boolean }

export type RepoResolution = {
  repo_id: string
  repo_type: string
  requested_revision: string
  commit_hash: string
  files: RepoFile[]
  total_bytes: number
}

export type TaskFile = {
  id: string
  path: string
  size: number
  status: string
  downloaded_bytes: number
  error?: string | null
}

export type DownloadTask = {
  id: string
  repo_id: string
  requested_revision: string
  commit_hash: string
  status: string
  total_bytes: number
  downloaded_bytes: number
  speed_bps: number
  eta_seconds?: number | null
  requires_token: boolean
  created_at: string
  updated_at: string
  error?: string | null
  files: TaskFile[]
}

export type AppSettings = {
  max_concurrent_files: number
  max_storage_bytes: number
  min_free_bytes: number
  retention_days: number
  allow_delete_files: boolean
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
  resolveRepo: (source: string, hfToken: string) =>
    request<RepoResolution>("/api/repos/resolve", {
      method: "POST",
      body: JSON.stringify({ source, hf_token: hfToken || null }),
    }),
  createTask: (source: string, selectedFiles: string[], hfToken: string) =>
    request<DownloadTask>("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ source, selected_files: selectedFiles, hf_token: hfToken || null }),
    }),
  tasks: () => request<DownloadTask[]>("/api/tasks"),
  command: (taskId: string, command: "pause" | "resume" | "retry" | "cancel", hfToken = "") =>
    request<DownloadTask>(`/api/tasks/${taskId}/${command}`, {
      method: "POST",
      body: JSON.stringify({ hf_token: hfToken || null }),
    }),
  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (settings: AppSettings) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
  deleteTask: (taskId: string, deleteFiles = true) =>
    fetch(`/api/tasks/${encodeURIComponent(taskId)}?delete_files=${deleteFiles}`, { method: "DELETE" }).then(async (response) => {
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.detail || "刪除失敗")
      }
    }),
}

export function fileDownloadUrl(taskId: string, path: string): string {
  const encoded = path.split("/").map(encodeURIComponent).join("/")
  return `/api/files/${encodeURIComponent(taskId)}/${encoded}`
}
