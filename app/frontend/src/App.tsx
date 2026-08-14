import {
  Activity,
  Archive,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleGauge,
  Download,
  ExternalLink,
  FileDown,
  FolderOpen,
  HardDrive,
  Images,
  KeyRound,
  Library,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Settings as SettingsIcon,
  ShieldCheck,
  Square,
  Trash2,
  X,
  XCircle,
  Zap,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { FileTree } from "@/components/file-tree"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { api, fileDownloadUrl, type AppSettings, type DownloadTask, type LibraryFile, type LibraryItem, type RepoResolution, type TaskInspection } from "@/lib/api"
import { cn, formatBytes, formatEta, percent } from "@/lib/utils"

type Page = "huggingface" | "civitai" | "transfers" | "library" | "settings"
type DownloadProvider = "huggingface" | "civitai"
type SourceCategory = "hf-model" | "hf-dataset" | "civitai-model"

const categoryLabels: Record<SourceCategory, string> = {
  "hf-model": "Hugging Face Model",
  "hf-dataset": "Hugging Face Dataset",
  "civitai-model": "Civitai Model",
}

function sourceCategory(provider: string, repoType: string): SourceCategory {
  if (provider === "civitai") return "civitai-model"
  return repoType === "dataset" ? "hf-dataset" : "hf-model"
}

function SourceBadge({ provider, repoType }: { provider: string; repoType: string }) {
  const category = sourceCategory(provider, repoType)
  return <Badge className={category === "civitai-model" ? "border-amber-400/25 bg-amber-400/10 text-amber-300" : category === "hf-dataset" ? "border-violet-400/20 bg-violet-400/10 text-violet-300" : "border-sky-400/20 bg-sky-400/10 text-sky-300"}>{categoryLabels[category]}</Badge>
}

function sourceCardClass(provider: string, repoType: string): string {
  const category = sourceCategory(provider, repoType)
  if (category === "civitai-model") return "border-amber-400/20 bg-gradient-to-r from-amber-400/[.055] via-[#101821] to-[#101821]"
  if (category === "hf-dataset") return "border-violet-400/20 bg-gradient-to-r from-violet-400/[.055] via-[#101821] to-[#101821]"
  return "border-sky-400/20 bg-gradient-to-r from-sky-400/[.055] via-[#101821] to-[#101821]"
}

function newestTaskFirst(left: DownloadTask, right: DownloadTask): number {
  const leftTime = Date.parse(left.created_at)
  const rightTime = Date.parse(right.created_at)
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) return rightTime - leftTime
  return right.id.localeCompare(left.id)
}

function CategoryTabs({ value, onChange, counts, includeAll = false }: { value: SourceCategory | "all"; onChange: (value: SourceCategory | "all") => void; counts: Record<SourceCategory, number>; includeAll?: boolean }) {
  const categories = Object.keys(categoryLabels) as SourceCategory[]
  return <div className="mb-5 flex flex-wrap gap-2">{includeAll && <Button size="sm" variant={value === "all" ? "default" : "secondary"} onClick={() => onChange("all")}>全部</Button>}{categories.map((category) => <Button key={category} size="sm" variant={value === category ? "default" : "secondary"} onClick={() => onChange(category)}>{categoryLabels[category]} <span className="ml-1 text-[10px] opacity-60">{counts[category]}</span></Button>)}</div>
}

type NewDownloadDraft = {
  source: string
  token: string
  includeGlobs: string
  excludeGlobs: string
  versionId: string
  repo: RepoResolution | null
  selected: Set<string>
}

type CreationBehavior = {
  clearAfterCreate: boolean
  openTransfersAfterCreate: boolean
}

const defaultCreationBehavior: CreationBehavior = {
  clearAfterCreate: false,
  openTransfersAfterCreate: false,
}

function storedCreationBehavior(): CreationBehavior {
  try {
    const stored = JSON.parse(window.localStorage.getItem("hfdm.creation-behavior") || "{}")
    return {
      clearAfterCreate: stored.clearAfterCreate === true,
      openTransfersAfterCreate: stored.openTransfersAfterCreate === true,
    }
  } catch {
    return defaultCreationBehavior
  }
}

function storedValue<T>(key: string, fallback: T): T {
  try {
    const value = window.localStorage.getItem(key)
    return value ? JSON.parse(value) as T : fallback
  } catch {
    return fallback
  }
}

function useStoredState<T>(key: string, fallback: T): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => storedValue(key, fallback))
  useEffect(() => { window.localStorage.setItem(key, JSON.stringify(value)) }, [key, value])
  return [value, setValue]
}

function storedDraft(provider: DownloadProvider): NewDownloadDraft {
  const stored = storedValue<Partial<NewDownloadDraft>>(`hfdm.${provider}.options`, {})
  return {
    ...emptyDownloadDraft(),
    source: typeof stored.source === "string" ? stored.source : "",
    includeGlobs: typeof stored.includeGlobs === "string" ? stored.includeGlobs : "",
    excludeGlobs: typeof stored.excludeGlobs === "string" ? stored.excludeGlobs : "",
    versionId: typeof stored.versionId === "string" ? stored.versionId : "",
  }
}

function emptyDownloadDraft(): NewDownloadDraft {
  return {
    source: "",
    token: "",
    includeGlobs: "",
    excludeGlobs: "",
    versionId: "",
    repo: null,
    selected: new Set(),
  }
}

const statusMeta: Record<string, { label: string; className: string }> = {
  queued: { label: "等待中", className: "border-sky-400/20 bg-sky-400/10 text-sky-300" },
  resolving: { label: "解析中", className: "border-sky-400/20 bg-sky-400/10 text-sky-300" },
  downloading: { label: "下載中", className: "border-cyan-400/20 bg-cyan-400/10 text-cyan-300" },
  pausing: { label: "暫停中", className: "border-amber-400/20 bg-amber-400/10 text-amber-300" },
  paused: { label: "已暫停", className: "border-amber-400/20 bg-amber-400/10 text-amber-300" },
  auth_required: { label: "需要 Token", className: "border-violet-400/20 bg-violet-400/10 text-violet-300" },
  completed: { label: "已完成", className: "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" },
  partial: { label: "部分完成", className: "border-orange-400/20 bg-orange-400/10 text-orange-300" },
  failed: { label: "失敗", className: "border-rose-400/20 bg-rose-400/10 text-rose-300" },
  cancelled: { label: "已取消", className: "border-slate-400/20 bg-slate-400/10 text-slate-400" },
}

const availabilityMeta: Record<string, { label: string; className: string }> = {
  available: { label: "Available", className: "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" },
  partial: { label: "Partial", className: "border-orange-400/20 bg-orange-400/10 text-orange-300" },
  moved: { label: "Moved", className: "border-slate-400/20 bg-slate-400/10 text-slate-300" },
  changed: { label: "Changed", className: "border-rose-400/20 bg-rose-400/10 text-rose-300" },
  unknown: { label: "Unknown", className: "border-violet-400/20 bg-violet-400/10 text-violet-300" },
}

function App() {
  const [page, setPage] = useStoredState<Page>("hfdm.active-page", "huggingface")
  const [hfDraft, setHfDraft] = useState<NewDownloadDraft>(() => storedDraft("huggingface"))
  const [civitaiDraft, setCivitaiDraft] = useState<NewDownloadDraft>(() => storedDraft("civitai"))
  const [creationBehavior, setCreationBehavior] = useState<CreationBehavior>(storedCreationBehavior)
  const [tasks, setTasks] = useState<DownloadTask[]>([])
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [isAdmin, setIsAdmin] = useState(false)
  const [online, setOnline] = useState(false)

  const refreshTasks = useCallback(async () => {
    try {
      const [nextTasks, nextLibrary] = await Promise.all([api.tasks(), api.library()])
      setTasks(nextTasks)
      setLibrary(nextLibrary)
      setOnline(true)
    } catch {
      setOnline(false)
    }
  }, [])

  useEffect(() => {
    void refreshTasks()
    void api.identity().then((result) => setIsAdmin(result.is_admin)).catch(() => setIsAdmin(false))
    const events = new EventSource("/api/events")
    let timer: number | undefined
    events.addEventListener("update", () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => void refreshTasks(), 120)
    })
    events.addEventListener("ready", () => setOnline(true))
    events.onerror = () => setOnline(false)
    return () => {
      events.close()
      window.clearTimeout(timer)
    }
  }, [refreshTasks])

  useEffect(() => {
    window.localStorage.setItem("hfdm.creation-behavior", JSON.stringify(creationBehavior))
  }, [creationBehavior])

  useEffect(() => {
    const { source, includeGlobs, excludeGlobs, versionId } = hfDraft
    window.localStorage.setItem("hfdm.huggingface.options", JSON.stringify({ source, includeGlobs, excludeGlobs, versionId }))
  }, [hfDraft.source, hfDraft.includeGlobs, hfDraft.excludeGlobs, hfDraft.versionId])

  useEffect(() => {
    const { source, includeGlobs, excludeGlobs, versionId } = civitaiDraft
    window.localStorage.setItem("hfdm.civitai.options", JSON.stringify({ source, includeGlobs, excludeGlobs, versionId }))
  }, [civitaiDraft.source, civitaiDraft.includeGlobs, civitaiDraft.excludeGlobs, civitaiDraft.versionId])

  const activeCount = tasks.filter((task) => ["queued", "downloading", "pausing"].includes(task.status)).length

  return (
    <div className="relative flex min-h-screen bg-[#080c11] text-slate-100">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-white/[.07] bg-[#0a0f15]/95 px-4 py-5 backdrop-blur-xl lg:flex">
        <div className="flex items-center gap-3 px-2 pb-8">
          <div className="grid size-10 place-items-center rounded-xl border border-cyan-300/20 bg-cyan-400/10 shadow-[inset_0_0_20px_rgba(34,211,238,.08)]">
            <Zap className="size-5 text-cyan-300" />
          </div>
          <div>
            <div className="text-base font-bold tracking-[.2em] text-white">HFDM</div>
            <div className="text-[10px] uppercase tracking-[.2em] text-slate-600">Model Relay</div>
          </div>
        </div>
        <nav className="space-y-1">
          <NavButton active={page === "huggingface"} icon={Download} label="從 Hugging Face 下載" onClick={() => setPage("huggingface")} />
          <NavButton active={page === "civitai"} icon={Box} label="從 Civitai 下載" onClick={() => setPage("civitai")} />
          <NavButton active={page === "transfers"} icon={Activity} label="傳輸任務" count={activeCount} onClick={() => setPage("transfers")} />
          <NavButton active={page === "library"} icon={Library} label="內容庫" onClick={() => setPage("library")} />
          <NavButton active={page === "settings"} icon={SettingsIcon} label="服務設定" onClick={() => setPage("settings")} />
        </nav>
        <div className="mt-auto rounded-xl border border-white/[.06] bg-white/[.025] p-3.5">
          <div className="mb-2 flex items-center justify-between text-xs">
            <span className="text-slate-500">Service</span>
            <span className={cn("flex items-center gap-1.5 font-medium", online ? "text-emerald-400" : "text-rose-400")}>
              <span className={cn("size-1.5 rounded-full", online ? "bg-emerald-400" : "bg-rose-400", online && "pulse-soft")} />
              {online ? "ONLINE" : "OFFLINE"}
            </span>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500"><Server className="size-3.5" /> LAN · {isAdmin ? "Admin" : "Visitor"}</div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 lg:ml-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-white/[.06] bg-[#080c11]/80 px-4 backdrop-blur-xl md:px-8">
          <div className="flex items-center gap-2 lg:hidden">
            <Zap className="size-5 text-cyan-300" /><span className="font-bold tracking-widest">HFDM</span>
          </div>
          <div className="hidden items-center gap-2 text-xs text-slate-500 lg:flex"><ShieldCheck className="size-4 text-emerald-400/70" /> Trusted LAN mode</div>
          <div className="flex items-center gap-2">
            <Badge className={isAdmin ? "text-cyan-300" : "text-slate-400"}>{isAdmin ? "Administrator" : "Visitor"}</Badge>
            <Button variant="ghost" size="icon" onClick={() => void refreshTasks()} aria-label="重新整理"><RefreshCw className="size-4" /></Button>
          </div>
        </header>

        <div className="mx-auto max-w-7xl px-4 py-7 md:px-8 md:py-10">
          {page === "huggingface" && <NewDownload provider="huggingface" draft={hfDraft} setDraft={setHfDraft} tasks={tasks} creationBehavior={creationBehavior} setCreationBehavior={setCreationBehavior} onCreated={() => { if (creationBehavior.clearAfterCreate) setHfDraft(emptyDownloadDraft()); void refreshTasks(); if (creationBehavior.openTransfersAfterCreate) setPage("transfers") }} />}
          {page === "civitai" && <NewDownload provider="civitai" draft={civitaiDraft} setDraft={setCivitaiDraft} tasks={tasks} creationBehavior={creationBehavior} setCreationBehavior={setCreationBehavior} onCreated={() => { if (creationBehavior.clearAfterCreate) setCivitaiDraft(emptyDownloadDraft()); void refreshTasks(); if (creationBehavior.openTransfersAfterCreate) setPage("transfers") }} />}
          {page === "transfers" && <Transfers tasks={tasks} isAdmin={isAdmin} refresh={refreshTasks} />}
          {page === "library" && <LibraryPage items={library} isAdmin={isAdmin} refresh={refreshTasks} />}
          {page === "settings" && <SettingsPage isAdmin={isAdmin} />}
        </div>
      </main>

      <nav className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-5 rounded-2xl border border-white/10 bg-[#0c1219]/95 p-1.5 shadow-2xl backdrop-blur-xl lg:hidden">
        <MobileNav active={page === "huggingface"} icon={Download} label="HF" onClick={() => setPage("huggingface")} />
        <MobileNav active={page === "civitai"} icon={Box} label="Civitai" onClick={() => setPage("civitai")} />
        <MobileNav active={page === "transfers"} icon={Activity} label="任務" onClick={() => setPage("transfers")} />
        <MobileNav active={page === "library"} icon={Library} label="內容" onClick={() => setPage("library")} />
        <MobileNav active={page === "settings"} icon={SettingsIcon} label="設定" onClick={() => setPage("settings")} />
      </nav>
    </div>
  )
}

function NavButton({ icon: Icon, label, active, count, onClick }: { icon: typeof Plus; label: string; active: boolean; count?: number; onClick: () => void }) {
  return <button onClick={onClick} className={cn("flex h-11 w-full items-center gap-3 rounded-lg px-3 text-sm transition", active ? "bg-cyan-400/10 text-cyan-300" : "text-slate-500 hover:bg-white/[.04] hover:text-slate-200")}><Icon className="size-[18px]" /><span className="flex-1 text-left font-medium">{label}</span>{Boolean(count) && <span className="rounded bg-cyan-400/15 px-1.5 py-0.5 text-[10px] text-cyan-300">{count}</span>}</button>
}

function MobileNav({ icon: Icon, label, active, onClick }: { icon: typeof Plus; label: string; active: boolean; onClick: () => void }) {
  return <button onClick={onClick} className={cn("flex flex-col items-center gap-1 rounded-xl py-2 text-[10px]", active ? "bg-cyan-400/10 text-cyan-300" : "text-slate-500")}><Icon className="size-4" />{label}</button>
}

function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="mb-7 flex flex-col justify-between gap-4 md:flex-row md:items-end"><div><div className="mb-2 text-[10px] font-bold uppercase tracking-[.24em] text-cyan-400/70">{eyebrow}</div><h1 className="text-2xl font-semibold tracking-tight text-white md:text-3xl">{title}</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">{description}</p></div>{action}</div>
}

function NewDownload({ provider, draft, setDraft, tasks, creationBehavior, setCreationBehavior, onCreated }: { provider: DownloadProvider; draft: NewDownloadDraft; setDraft: React.Dispatch<React.SetStateAction<NewDownloadDraft>>; tasks: DownloadTask[]; creationBehavior: CreationBehavior; setCreationBehavior: React.Dispatch<React.SetStateAction<CreationBehavior>>; onCreated: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [createdNotice, setCreatedNotice] = useState("")
  const { source, token, includeGlobs, excludeGlobs, versionId, repo, selected } = draft
  const isCivitai = provider === "civitai"
  const selectedBytes = useMemo(() => repo?.files.filter((file) => selected.has(file.path)).reduce((sum, file) => sum + file.size, 0) ?? 0, [repo, selected])
  const matchingTasks = useMemo(() => repo ? tasks.filter((task) => task.provider === repo.provider && task.repo_type === repo.repo_type && task.repo_id === repo.repo_id && task.commit_hash === repo.commit_hash) : [], [repo, tasks])
  const existingPaths = useMemo(() => new Set(matchingTasks.flatMap((task) => task.files.map((file) => file.path))), [matchingTasks])
  const selectedExistingCount = [...selected].filter((path) => existingPaths.has(path)).length
  const selectedNewCount = selected.size - selectedExistingCount

  const updateDraft = (values: Partial<NewDownloadDraft>) => {
    setDraft((current) => ({ ...current, ...values }))
  }
  const clearDraft = () => {
    setDraft(emptyDownloadDraft())
    setError("")
    setCreatedNotice("")
  }

  const resolve = async (nextVersionId?: number) => {
    setBusy(true); setError(""); setCreatedNotice("")
    try {
      const requestedVersionId = nextVersionId ?? (isCivitai && versionId ? Number(versionId) : undefined)
      const result = await api.resolveRepo(
        source,
        token,
        splitGlobInput(includeGlobs),
        splitGlobInput(excludeGlobs),
        requestedVersionId,
      )
      if (result.provider !== provider) throw new Error(`此入口只接受 ${isCivitai ? "Civitai model URL" : "Hugging Face Model／Dataset URL"}`)
      updateDraft({ repo: result, versionId: result.provider === "civitai" ? result.commit_hash : "", selected: new Set(result.suggested_files) })
    } catch (reason) { setError(reason instanceof Error ? reason.message : "讀取 repo 失敗") }
    finally { setBusy(false) }
  }
  const create = async () => {
    if (!repo || !selected.size) return
    setBusy(true); setError(""); setCreatedNotice("")
    try {
      await api.createTask(source, [...selected], token, repo.provider === "civitai" ? Number(versionId) : undefined)
      setCreatedNotice(creationBehavior.clearAfterCreate ? "下載任務已建立；目前設定已依偏好清除。" : "下載任務已建立；目前解析與選檔設定已保留。")
      onCreated()
    } catch (reason) { setError(reason instanceof Error ? reason.message : "建立任務失敗") }
    finally { setBusy(false) }
  }

  return <>
    <PageHeading eyebrow={isCivitai ? "Civitai download" : "Hugging Face download"} title={isCivitai ? "從 Civitai 下載模型" : "從 Hugging Face 下載"} description={isCivitai ? "貼上 Civitai model 網址；HFDM 會列出模型系列、目前版本的檔案變體，以及可用的範例圖片與生成資訊。" : "貼上 Hugging Face Model 或 Dataset 網址；解析草稿與 Token 只保留於目前記憶體。"} action={<Button variant="ghost" onClick={clearDraft} disabled={busy || (!source && !token && !includeGlobs && !excludeGlobs && !repo)}><Trash2 className="size-4" />清除草稿</Button>} />
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Card>
        <CardHeader><h2 className="font-semibold text-white">{isCivitai ? "Civitai 模型網址" : "Hugging Face 來源網址"}</h2><p className="text-xs text-slate-500">{isCivitai ? "例如：https://civitai.com/models/620406/...?modelVersionId=3161628" : "支援 Hugging Face Model／Dataset 與 /tree/<revision> 網址"}</p></CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row"><Input value={source} onChange={(event) => updateDraft({ source: event.target.value, repo: null, versionId: "" })} placeholder={isCivitai ? "貼上 Civitai model URL" : "貼上 Hugging Face owner/repo 或 URL"} onKeyDown={(event) => event.key === "Enter" && void resolve()} /><Button className="sm:w-28" onClick={() => void resolve()} disabled={busy || !source.trim()}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}解析</Button></div>
          <div className="relative"><KeyRound className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-600" /><Input className="pl-10" type="password" autoComplete="off" value={token} onChange={(event) => updateDraft({ token: event.target.value })} placeholder={`${isCivitai ? "Civitai API" : "HF"} Token（選填；只存於記憶體）`} /></div>
          {!isCivitai && <><div className="grid gap-2 sm:grid-cols-2"><Input value={includeGlobs} onChange={(event) => updateDraft({ includeGlobs: event.target.value })} placeholder="Include glob，例如 **/*.parquet" /><Input value={excludeGlobs} onChange={(event) => updateDraft({ excludeGlobs: event.target.value })} placeholder="Exclude glob，例如 data/test/**" /></div><div className="text-[11px] leading-5 text-slate-600">多個 glob 可用逗號或換行分隔；重新按「解析」即可預覽套用後的選檔與容量。</div></>}
          {error && <div className="flex items-start gap-2 rounded-lg border border-rose-400/15 bg-rose-400/[.07] p-3 text-sm text-rose-300"><XCircle className="mt-0.5 size-4 shrink-0" />{error}</div>}
          {createdNotice && <div className="flex items-start gap-2 rounded-lg border border-emerald-400/15 bg-emerald-400/[.07] p-3 text-sm text-emerald-300"><CheckCircle2 className="mt-0.5 size-4 shrink-0" />{createdNotice}</div>}
          {repo && <div className="space-y-4 pt-2">
            <div className="flex flex-wrap items-center gap-2"><Badge className="border-cyan-400/20 bg-cyan-400/10 text-cyan-300">{repo.display_name || repo.repo_id}</Badge><Badge>{repo.provider === "civitai" ? "Civitai" : "Hugging Face"}</Badge><Badge className={repo.repo_type === "dataset" ? "border-violet-400/20 bg-violet-400/10 text-violet-300" : ""}>{repo.repo_type === "dataset" ? "Dataset" : "Model"}</Badge><Badge>{repo.version_name || repo.requested_revision}</Badge><span className="font-mono text-[10px] text-slate-600">{repo.commit_hash.slice(0, 12)}</span></div>
            {repo.provider === "civitai" && repo.versions.length > 0 && <label className="block rounded-lg border border-white/[.07] bg-white/[.025] p-3 text-xs text-slate-400"><span className="mb-1 block font-medium text-slate-300">模型系列／版本</span><span className="mb-3 block text-[11px] text-slate-600">切換後會重新列出該版本實際提供的精度與格式檔案。</span><select aria-label="模型系列／版本" className="h-10 w-full rounded-lg border border-white/10 bg-[#0a1016] px-3 text-sm text-slate-200" value={versionId} onChange={(event) => { const next = event.target.value; updateDraft({ versionId: next }); void resolve(Number(next)) }}>{repo.versions.map((version) => <option key={version.id} value={version.id}>{version.name}{version.base_model ? ` · ${version.base_model}` : ""}</option>)}</select></label>}
            {repo.provider === "civitai" && <div className="rounded-lg border border-cyan-400/15 bg-cyan-400/[.04] p-3 text-xs leading-5 text-slate-400">{String(repo.provider_metadata.model_type || "Model")}{repo.provider_metadata.base_model ? ` · ${String(repo.provider_metadata.base_model)}` : ""}{repo.provider_metadata.creator ? ` · by ${String(repo.provider_metadata.creator)}` : ""}。完成時會驗證 Civitai 提供的 SHA256。</div>}
            {repo.repo_type === "dataset" && repo.files.length >= 100 && <div className="rounded-lg border border-amber-400/15 bg-amber-400/[.05] p-3 text-xs leading-5 text-amber-200">此 Dataset 含有 {repo.files.length} 個檔案。大量小檔會增加排程與磁碟負擔；可使用 glob 縮小範圍，並在「服務設定」調整同時下載檔案數。</div>}
            {repo.provider === "civitai" && <div className="text-xs font-medium text-slate-300">此版本的檔案變體與附加內容</div>}
            {matchingTasks.length > 0 && <div className={cn("rounded-lg border p-3 text-xs leading-5", selectedNewCount ? "border-amber-400/15 bg-amber-400/[.05] text-amber-200" : "border-emerald-400/15 bg-emerald-400/[.05] text-emerald-300")}>
              內容庫／任務中已有此來源版本的 {existingPaths.size} 個檔案；目前選取中 {selectedExistingCount} 個已存在、{selectedNewCount} 個將新增。{selectedNewCount ? "建立時會合併至同一來源版本，不會另留重複任務。" : "目前設定已存在，不需要重複建立。"}
            </div>}
            <FileTree files={repo.files} selected={selected} onChange={(next) => updateDraft({ selected: next })} />
            <div className="space-y-3 rounded-xl border border-white/[.07] bg-white/[.025] p-4">
              <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center"><div><div className="text-sm font-medium text-slate-200">已選 {selected.size} / {repo.files.length} 個檔案</div><div className="mt-1 text-xs text-slate-500">合計 {formatBytes(selectedBytes)} · 儲存於 download/{repo.provider === "civitai" ? "civitai/models" : repo.repo_type === "dataset" ? "datasets" : "models"}/</div></div><Button onClick={() => void create()} disabled={busy || !selected.size || (matchingTasks.length > 0 && selectedNewCount === 0)}><Play className="size-4 fill-current" />{matchingTasks.length > 0 ? selectedNewCount ? "合併新增檔案" : "設定已存在" : "建立下載任務"}</Button></div>
              <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-white/[.05] pt-3 text-xs text-slate-400">
                <label className="flex cursor-pointer items-center gap-2"><Checkbox checked={creationBehavior.clearAfterCreate} onCheckedChange={(checked) => setCreationBehavior((current) => ({ ...current, clearAfterCreate: checked }))} />建立後清除目前設定</label>
                <label className="flex cursor-pointer items-center gap-2"><Checkbox checked={creationBehavior.openTransfersAfterCreate} onCheckedChange={(checked) => setCreationBehavior((current) => ({ ...current, openTransfersAfterCreate: checked }))} />建立後前往傳輸任務</label>
                <span className="text-slate-600">偏好會保留；Token 與解析內容仍只存於目前記憶體。</span>
              </div>
            </div>
          </div>}
        </CardContent>
      </Card>
      <div className="space-y-4">
        <MetricCard icon={Archive} label="Repo files" value={repo ? String(repo.files.length) : "—"} note={repo ? formatBytes(repo.total_bytes) : "等待解析"} />
        <MetricCard icon={HardDrive} label="Selected" value={repo ? formatBytes(selectedBytes) : "—"} note="固定保存於 download/" />
        <Card className="border-cyan-400/10 bg-cyan-400/[.035]"><CardContent className="p-4"><div className="mb-2 flex items-center gap-2 text-xs font-semibold text-cyan-300"><ShieldCheck className="size-4" />LAN 共用模式</div><p className="text-xs leading-5 text-slate-500">完成內容會對可信任 LAN 開放。相同 commit 與檔案會共用既有下載。</p></CardContent></Card>
      </div>
    </div>
  </>
}

function splitGlobInput(value: string): string[] {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean)
}

function MetricCard({ icon: Icon, label, value, note }: { icon: typeof Archive; label: string; value: string; note: string }) {
  return <Card><CardContent className="p-4"><div className="mb-4 flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-[.18em] text-slate-600">{label}</span><Icon className="size-4 text-cyan-400/60" /></div><div className="text-xl font-semibold text-slate-100">{value}</div><div className="mt-1 text-xs text-slate-600">{note}</div></CardContent></Card>
}

function TransferOverview({ tasks }: { tasks: DownloadTask[] }) {
  const pending = tasks.filter((task) => !["completed", "failed", "cancelled"].includes(task.status))
  const currentSpeed = tasks.filter((task) => ["downloading", "pausing"].includes(task.status)).reduce((sum, task) => sum + task.speed_bps, 0)
  const downloadedBytes = pending.reduce((sum, task) => sum + task.downloaded_bytes, 0)
  const totalBytes = pending.reduce((sum, task) => sum + task.total_bytes, 0)
  const activeCount = pending.filter((task) => ["downloading", "pausing"].includes(task.status)).length
  const queuedCount = pending.filter((task) => task.status === "queued").length
  const [peakSpeed, setPeakSpeed] = useState(0)
  const [samples, setSamples] = useState<number[]>(() => Array(30).fill(0))
  const speedRef = useRef(0)
  speedRef.current = currentSpeed
  useEffect(() => {
    setPeakSpeed((peak) => Math.max(peak, currentSpeed))
  }, [currentSpeed])
  useEffect(() => {
    const timer = window.setInterval(() => {
      const speed = speedRef.current
      setSamples((current) => [...current.slice(-59), speed])
      setPeakSpeed((peak) => Math.max(peak, speed))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])
  const chartPoints = useMemo(() => {
    const ceiling = Math.max(...samples, 1)
    return samples.map((sample, index) => `${(index / Math.max(samples.length - 1, 1)) * 100},${34 - (sample / ceiling) * 31}`).join(" ")
  }, [samples])
  const overallProgress = percent(downloadedBytes, totalBytes)

  return <Card className="mb-5 overflow-hidden border-cyan-400/15 bg-gradient-to-br from-cyan-400/[.065] via-[#111a24] to-[#0d141d]">
    <CardContent className="p-0">
      <div className="grid gap-5 p-5 lg:grid-cols-[minmax(260px,1.2fr)_minmax(420px,1.8fr)]">
        <div className="flex min-w-0 flex-col justify-between">
          <div><div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[.2em] text-cyan-300/75"><Download className="size-4" />整體下載進度</div><div className="mt-2 flex items-end gap-3"><span className="text-3xl font-semibold tabular-nums text-white">{overallProgress}%</span><span className="pb-1 text-xs text-slate-500">{pending.length ? `${pending.length} 個未結束任務` : "目前沒有待處理任務"}</span></div></div>
          <div className="mt-5"><Progress value={overallProgress} className="h-2" /><div className="mt-2 flex justify-between gap-3 text-xs tabular-nums text-slate-500"><span>{formatBytes(downloadedBytes)} 已下載</span><span>{formatBytes(totalBytes)} 總量</span></div></div>
        </div>
        <div className="grid gap-4 sm:grid-cols-[minmax(200px,1fr)_minmax(220px,1.4fr)]">
          <div className="grid grid-cols-2 gap-x-5 gap-y-4">
            <OverviewMetric label="目前速度" value={`${formatBytes(currentSpeed)}/s`} />
            <OverviewMetric label="本次峰值" value={`${formatBytes(peakSpeed)}/s`} />
            <OverviewMetric label="佇列總量" value={formatBytes(totalBytes)} />
            <OverviewMetric label="執行中 / 等待" value={`${activeCount} / ${queuedCount}`} />
          </div>
          <div className="min-h-28 rounded-xl border border-white/[.06] bg-black/15 p-3"><div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-[.16em] text-slate-600"><span>速度歷史</span><span>最近 60 秒</span></div><svg viewBox="0 0 100 36" preserveAspectRatio="none" className="h-20 w-full" role="img" aria-label="最近六十秒的整體下載速度"><defs><linearGradient id="transfer-speed-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#22d3ee" stopOpacity="0.28" /><stop offset="100%" stopColor="#22d3ee" stopOpacity="0" /></linearGradient></defs><polygon points={`0,36 ${chartPoints} 100,36`} fill="url(#transfer-speed-fill)" /><polyline points={chartPoints} fill="none" stroke="#22d3ee" strokeWidth="1.2" vectorEffect="non-scaling-stroke" /></svg></div>
        </div>
      </div>
    </CardContent>
  </Card>
}

function OverviewMetric({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><div className="text-[10px] font-bold uppercase tracking-[.15em] text-slate-600">{label}</div><div className="mt-1 truncate text-sm font-semibold tabular-nums text-slate-200" title={value}>{value}</div></div>
}

type TransferEntry =
  | { kind: "task"; task: DownloadTask }
  | { kind: "civitai"; tasks: DownloadTask[] }

function Transfers({ tasks, isAdmin, refresh }: { tasks: DownloadTask[]; isAdmin: boolean; refresh: () => Promise<void> }) {
  const [category, setCategory] = useStoredState<SourceCategory | "all">("hfdm.transfers.category", "all")
  const active = tasks.filter((task) => !["completed", "failed", "cancelled"].includes(task.status))
  const counts = useMemo(() => ({
    "hf-model": tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === "hf-model").length,
    "hf-dataset": tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === "hf-dataset").length,
    "civitai-model": new Set(tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === "civitai-model").map((task) => task.repo_id)).size,
  }), [tasks])
  const visible = useMemo(() => (category === "all" ? [...tasks] : tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === category)).sort(newestTaskFirst), [category, tasks])
  const transferEntries = useMemo(() => {
    const groupedCivitai = new Set<string>()
    const entries: TransferEntry[] = []
    for (const task of visible) {
      if (task.provider !== "civitai") {
        entries.push({ kind: "task", task })
        continue
      }
      if (groupedCivitai.has(task.repo_id)) continue
      groupedCivitai.add(task.repo_id)
      entries.push({ kind: "civitai", tasks: visible.filter((candidate) => candidate.provider === "civitai" && candidate.repo_id === task.repo_id) })
    }
    return entries
  }, [visible])
  return <><PageHeading eyebrow="Transfers" title="下載任務" description="即時查看服務端取得進度。暫停會停止排程並終止正在執行的檔案 worker。" action={<div className="flex items-center gap-2 text-xs text-slate-500"><CircleGauge className="size-4 text-cyan-400" />{active.length} active</div>} />
    <TransferOverview tasks={tasks} />
    <CategoryTabs value={category} onChange={setCategory} counts={counts} includeAll />
    <div className="space-y-4">{transferEntries.length ? transferEntries.map((entry) => entry.kind === "task" ? <TaskCard key={entry.task.id} task={entry.task} isAdmin={isAdmin} refresh={refresh} /> : <CivitaiTaskGroup key={entry.tasks[0].repo_id} tasks={entry.tasks} isAdmin={isAdmin} refresh={refresh} />) : <EmptyState icon={Activity} title="此分類尚無下載任務" text="請從左側選擇 Hugging Face 或 Civitai 下載入口建立任務。" />}</div>
  </>
}

function CivitaiTaskGroup({ tasks, isAdmin, refresh }: { tasks: DownloadTask[]; isAdmin: boolean; refresh: () => Promise<void> }) {
  const versions = [...new Map(tasks.map((task) => [task.commit_hash, task])).values()]
  const duplicateCount = tasks.length - versions.length
  const activeVersions = versions.filter((task) => !["completed", "cancelled"].includes(task.status)).length
  const completedVersions = versions.filter((task) => task.status === "completed").length
  const totalFiles = new Set(tasks.flatMap((task) => task.files.map((file) => file.remote_id || file.path))).size
  return <Card className={cn("overflow-hidden", sourceCardClass("civitai", "model"))}>
    <div className="flex flex-col gap-3 border-b border-amber-400/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0"><div className="flex items-center gap-2"><Box className="size-5 shrink-0 text-amber-400" /><h3 className="truncate font-semibold text-white">{tasks[0].display_name || tasks[0].repo_id}</h3></div><div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 pl-7 text-xs text-slate-500"><span>Civitai · Model {tasks[0].repo_id}</span><span aria-hidden="true">·</span><span>{versions.length} 個版本</span><span aria-hidden="true">·</span><span>{totalFiles} 個檔案</span>{duplicateCount > 0 && <><span aria-hidden="true">·</span><span>{duplicateCount} 筆舊紀錄已折疊</span></>}</div></div>
      <div className="flex shrink-0 items-center gap-2 text-xs"><span className={cn("size-2 rounded-full", activeVersions ? "bg-cyan-400 pulse-soft" : completedVersions === versions.length ? "bg-emerald-400" : "bg-slate-500")} /><span className="text-slate-400">{activeVersions ? `${activeVersions} 個版本進行中` : completedVersions === versions.length ? "所有版本已完成" : `${completedVersions} / ${versions.length} 已完成`}</span></div>
    </div>
    <div className="divide-y divide-white/[.055]">{versions.map((task) => <TaskCard key={task.commit_hash} task={task} isAdmin={isAdmin} refresh={refresh} compact />)}</div>
  </Card>
}

function TaskCard({ task, isAdmin, refresh, compact = false }: { task: DownloadTask; isAdmin: boolean; refresh: () => Promise<void>; compact?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const [token, setToken] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [inspection, setInspection] = useState<TaskInspection | null>(null)
  const [editToken, setEditToken] = useState("")
  const [editSelected, setEditSelected] = useState<Set<string>>(new Set())
  const [editMessage, setEditMessage] = useState("")
  const meta = statusMeta[task.status] ?? { label: task.status, className: "" }
  const availability = availabilityMeta[task.local_availability] ?? availabilityMeta.unknown
  const progress = percent(task.downloaded_bytes, task.total_bytes)
  const command = async (action: "pause" | "resume" | "retry" | "cancel") => {
    setBusy(true); setError("")
    try { await api.command(task.id, action, task.provider, token); setToken(""); await refresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失敗") }
    finally { setBusy(false) }
  }
  const removeHistory = async () => {
    if (!window.confirm("只刪除這筆下載歷史嗎？實體檔案不會被刪除。")) return
    setBusy(true); setError("")
    try { await api.deleteTask(task.id, false); await refresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : "刪除失敗") }
    finally { setBusy(false) }
  }
  const removeFiles = async () => {
    if (!window.confirm("只刪除 HFDM download/ 內的實體檔案嗎？下載歷史會保留並顯示為 Moved。")) return
    setBusy(true); setError("")
    try { await api.deleteTaskFiles(task.id); await refresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : "刪除檔案失敗") }
    finally { setBusy(false) }
  }
  const inspectRepo = async (versionId?: number) => {
    setBusy(true); setError(""); setEditMessage("")
    try {
      const result = await api.inspectTask(task.id, task.provider, editToken, versionId)
      setInspection(result)
      const available = new Set(result.resolution.files.map((file) => file.path))
      setEditSelected(new Set(result.selected_files.filter((path) => available.has(path))))
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Repo 檢查失敗") }
    finally { setBusy(false) }
  }
  const openEditor = () => {
    setEditing(true); setExpanded(true); setInspection(null); setEditSelected(new Set()); setEditMessage(""); setError("")
    if (!task.requires_token) window.setTimeout(() => void inspectRepo(), 0)
  }
  const saveConfiguration = async () => {
    if (!inspection || !editSelected.size) return
    setBusy(true); setError(""); setEditMessage("")
    try {
      const versionId = inspection.resolution.provider === "civitai" ? Number(inspection.resolution.commit_hash) : undefined
      const result = await api.updateTaskConfiguration(task.id, [...editSelected], task.provider, editToken, versionId)
      setEditToken("")
      setEditMessage(result.created_new ? "已建立更新任務並取代原任務；實體檔案不受影響。" : "任務設定已更新。")
      setEditing(false)
      await refresh()
    } catch (reason) { setError(reason instanceof Error ? reason.message : "更新任務失敗") }
    finally { setBusy(false) }
  }
  const Container = compact ? "div" : Card
  const cardCategory = sourceCategory(task.provider, task.repo_type)
  return <Container className={cn("overflow-hidden", compact ? "bg-[#0d141c]/55" : sourceCardClass(task.provider, task.repo_type))}>
    <div className={compact ? "px-3 py-3 sm:px-4" : "p-5"}>
      <div className={cn("flex flex-col gap-3", compact ? "sm:flex-row sm:items-center" : "md:flex-row md:items-center md:gap-4")}>
        {!compact && <div className={cn("grid size-11 shrink-0 place-items-center rounded-xl border bg-[#0a1016]", cardCategory === "hf-dataset" ? "border-violet-400/15" : "border-sky-400/15")}><Box className={cn("size-5", cardCategory === "hf-dataset" ? "text-violet-400/80" : "text-sky-400/80")} /></div>}
        <button type="button" onClick={() => compact && setExpanded(!expanded)} className={cn("min-w-0 flex-1 text-left", compact && "group")}>
          <div className="flex flex-wrap items-center gap-2">
            {compact && <ChevronDown className={cn("size-4 shrink-0 text-slate-600 transition-transform group-hover:text-cyan-300", expanded && "rotate-180")} />}
            <h3 className="truncate font-semibold text-slate-100">{compact ? String(task.provider_metadata.version_name || task.requested_revision || task.commit_hash) : task.display_name || task.repo_id}</h3>
            {!compact && <SourceBadge provider={task.provider} repoType={task.repo_type} />}
            <Badge className={meta.className}>{meta.label}</Badge><Badge className={availability.className}>{availability.label}</Badge>
          </div>
          <div className={cn("mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-600", compact && "pl-6")}><span className="font-mono">{compact ? `Version ${task.commit_hash}` : task.commit_hash.slice(0, 10)}</span><span>{task.files.length} files</span><span>{formatBytes(task.total_bytes)}</span></div>
        </button>
        {isAdmin && <div className="flex items-center gap-1.5">
          <Button variant="secondary" size="sm" disabled={busy} onClick={openEditor}><SettingsIcon className="size-3.5" />編輯</Button>
          {["queued", "downloading"].includes(task.status) && <Button variant="secondary" size="sm" disabled={busy} onClick={() => void command("pause")}><Pause className="size-3.5" />暫停</Button>}
          {["paused", "auth_required"].includes(task.status) && <Button size="sm" disabled={busy} onClick={() => void command("resume")}><Play className="size-3.5" />繼續</Button>}
          {["failed", "partial"].includes(task.status) && <Button size="sm" disabled={busy} onClick={() => void command("retry")}><RotateCcw className="size-3.5" />重試</Button>}
          {!task.status.match(/completed|cancelled/) && <Button variant="ghost" size="icon" disabled={busy} onClick={() => void command("cancel")} aria-label="取消"><Square className="size-3.5" /></Button>}
          {["completed", "cancelled", "failed", "partial", "paused"].includes(task.status) && <Button variant="ghost" size="icon" disabled={busy || task.local_availability === "moved" || task.local_availability === "unknown"} onClick={() => void removeFiles()} aria-label="只刪除實體檔案" title="只刪除實體檔案"><HardDrive className="size-3.5" /></Button>}
          {["completed", "cancelled", "failed", "partial", "paused"].includes(task.status) && <Button variant="destructive" size="icon" disabled={busy} onClick={() => void removeHistory()} aria-label="只刪除歷史" title="只刪除歷史"><Trash2 className="size-3.5" /></Button>}
        </div>}
      </div>
      {task.status === "auth_required" && isAdmin && <div className="mt-4 flex gap-2"><Input type="password" autoComplete="off" className="h-9" placeholder={`重新提供 ${task.provider === "civitai" ? "Civitai API" : "HF"} Token`} value={token} onChange={(event) => setToken(event.target.value)} /><Button size="sm" onClick={() => void command("resume")} disabled={!token || busy}>驗證並繼續</Button></div>}
      <div className={compact ? "mt-3" : "mt-5"}><div className="mb-2 flex flex-wrap justify-between gap-2 text-xs"><span className="text-slate-500">{formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}</span><span className="flex gap-3 font-mono text-slate-500"><span>{task.status === "downloading" ? `${formatBytes(task.speed_bps)}/s` : "— B/s"}</span><span>ETA {task.status === "downloading" ? formatEta(task.eta_seconds) : "—"}</span><span className="text-slate-300">{progress}%</span></span></div><Progress value={progress} /></div>
      {(error || task.error) && <div className="mt-3 text-xs text-rose-300">{error || task.error}</div>}
      {editMessage && <div className="mt-3 text-xs text-emerald-300">{editMessage}</div>}
      {editing && <div className="mt-4 space-y-4 rounded-xl border border-cyan-400/15 bg-cyan-400/[.035] p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><div className="text-sm font-semibold text-slate-200">編輯下載任務</div><div className="mt-1 text-xs text-slate-500">重新解析 {task.repo_id} / {task.requested_revision}，檢查遠端 commit 與完整檔案樹。</div></div><Button variant="ghost" size="sm" onClick={() => { setEditing(false); setEditToken("") }}>關閉</Button></div>
        <div className="flex flex-col gap-2 sm:flex-row"><div className="relative min-w-0 flex-1"><KeyRound className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-600" /><Input className="pl-10" type="password" autoComplete="off" value={editToken} onChange={(event) => setEditToken(event.target.value)} placeholder={task.requires_token ? `輸入 ${task.provider === "civitai" ? "Civitai API" : "HF"} Token 後重新檢查` : `${task.provider === "civitai" ? "Civitai API" : "HF"} Token（選填，只存於記憶體）`} /></div><Button variant="secondary" onClick={() => void inspectRepo()} disabled={busy || (task.requires_token && !editToken)}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}檢查來源</Button></div>
        {inspection && <>{inspection.resolution.provider === "civitai" && inspection.resolution.versions.length > 0 && <select className="h-10 w-full rounded-lg border border-white/10 bg-[#0a1016] px-3 text-sm text-slate-200" value={inspection.resolution.commit_hash} onChange={(event) => void inspectRepo(Number(event.target.value))}>{inspection.resolution.versions.map((version) => <option key={version.id} value={version.id}>{version.name}{version.base_model ? ` · ${version.base_model}` : ""}</option>)}</select>}<div className="flex flex-wrap items-center gap-2 text-xs"><Badge>{inspection.resolution.version_name || inspection.resolution.requested_revision}</Badge><span className="font-mono text-slate-500">目前 {task.commit_hash.slice(0, 12)}</span><span className="text-slate-600">→</span><span className="font-mono text-slate-300">遠端 {inspection.resolution.commit_hash.slice(0, 12)}</span>{inspection.update_available ? <Badge className="border-amber-400/20 bg-amber-400/10 text-amber-300">來源有更新</Badge> : <Badge className="border-emerald-400/20 bg-emerald-400/10 text-emerald-300">已是相同版本</Badge>}</div>{inspection.unavailable_selected_files.length > 0 && <div className="rounded-lg border border-amber-400/15 bg-amber-400/[.06] p-3 text-xs text-amber-200">原任務有 {inspection.unavailable_selected_files.length} 個檔案已不在目前來源：{inspection.unavailable_selected_files.join(", ")}</div>}<FileTree files={inspection.resolution.files} selected={editSelected} onChange={setEditSelected} /><div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div className="text-xs text-slate-500">已選 {editSelected.size} / {inspection.resolution.files.length} 個檔案。{["downloading", "pausing"].includes(task.status) ? "請先暫停目前下載，再修改設定。" : inspection.can_update_in_place ? "將更新目前任務。" : "儲存時會建立新任務並取代原任務；舊實體檔案不會刪除。"}</div><Button onClick={() => void saveConfiguration()} disabled={busy || !editSelected.size || ["downloading", "pausing"].includes(task.status)}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}{["downloading", "pausing"].includes(task.status) ? "請先暫停" : inspection.can_update_in_place ? "儲存設定" : "建立更新任務"}</Button></div></>}
      </div>}
      {!compact && <button onClick={() => setExpanded(!expanded)} className="mt-4 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300"><ChevronDown className={cn("size-3.5 transition-transform", expanded && "rotate-180")} />{expanded ? "收合檔案" : "查看檔案"}</button>}
    </div>
    {expanded && <div className="max-h-72 overflow-auto border-t border-white/[.06] bg-[#080d13]/65">{task.files.map((file) => <div key={file.id} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 border-b border-white/[.035] px-4 py-2 text-xs sm:grid-cols-[auto_minmax(0,1fr)_auto_auto_auto]"><FileStatus status={file.status} /><span className="truncate text-slate-400">{file.path}</span><span className={cn("text-[10px] uppercase", file.local_status === "available" ? "text-emerald-400" : "text-slate-500")}>{file.local_status}</span><span className="col-start-2 font-mono text-[10px] text-slate-600 sm:col-start-auto">{formatBytes(file.size)}</span>{file.status === "completed" && file.local_status === "available" && <a className="col-start-3 row-start-2 text-cyan-400 hover:text-cyan-300 sm:col-start-auto sm:row-start-auto" href={fileDownloadUrl(task.id, file.path)} aria-label={`下載 ${file.path}`}><FileDown className="size-4" /></a>}</div>)}</div>}
  </Container>
}

function FileStatus({ status }: { status: string }) {
  if (status === "completed") return <CheckCircle2 className="size-4 shrink-0 text-emerald-400" />
  if (status === "downloading") return <LoaderCircle className="size-4 shrink-0 animate-spin text-cyan-400" />
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-rose-400" />
  if (status === "paused") return <Pause className="size-4 shrink-0 text-amber-400" />
  return <span className="size-2 shrink-0 rounded-full bg-slate-700" />
}

function metadataText(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key]
  return typeof value === "string" ? value : ""
}

function metadataList(metadata: Record<string, unknown>, key: string): string[] {
  const value = metadata[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : []
}

function CivitaiLibraryMetadata({ item, selectedTags, onTag, onModelType, onBaseModel, onCreator }: { item: LibraryItem; selectedTags: string[]; onTag: (tag: string) => void; onModelType: (value: string) => void; onBaseModel: (value: string) => void; onCreator: (value: string) => void }) {
  const tags = metadataList(item.provider_metadata, "tags")
  const modelType = metadataText(item.provider_metadata, "model_type")
  const baseModel = metadataText(item.provider_metadata, "base_model")
  const baseModelType = metadataText(item.provider_metadata, "base_model_type")
  const creator = metadataText(item.provider_metadata, "creator")
  return <div className="mb-3 space-y-2.5 rounded-lg border border-amber-300/10 bg-amber-300/[.025] p-3 text-xs">
    <div><div className="mb-1.5 text-[9px] font-bold uppercase tracking-[.18em] text-amber-300/45">模型屬性 · 點擊可過濾</div><div className="flex flex-wrap gap-1.5">{modelType && <button type="button" onClick={() => onModelType(modelType)} className="rounded-full border border-amber-300/20 bg-amber-300/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-amber-200 hover:bg-amber-300/20">Type · {modelType}</button>}{baseModel && <button type="button" onClick={() => onBaseModel(baseModel)} className="rounded-full border border-violet-300/20 bg-violet-300/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-violet-200 hover:bg-violet-300/20">Base · {baseModel}{baseModelType ? ` · ${baseModelType}` : ""}</button>}{creator && <button type="button" onClick={() => onCreator(creator)} className="rounded-full border border-sky-300/20 bg-sky-300/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-sky-200 hover:bg-sky-300/20">Creator · {creator}</button>}</div></div>
    {tags.length > 0 && <div className="border-t border-white/[.045] pt-2"><div className="mb-1.5 text-[9px] font-bold uppercase tracking-[.18em] text-slate-600">標籤 · 可多選</div><div className="flex flex-wrap gap-1.5">{tags.map((tag) => <button key={tag} type="button" aria-pressed={selectedTags.includes(tag)} onClick={() => onTag(tag)} className={cn("rounded-full border px-2 py-0.5 text-[10px] transition", selectedTags.includes(tag) ? "border-cyan-300/35 bg-cyan-300/15 text-cyan-200" : "border-white/[.07] bg-white/[.035] text-slate-400 hover:border-cyan-400/30 hover:text-cyan-300")}>#{tag}</button>)}</div></div>}
  </div>
}

function LibraryVersionCard({ item, isAdmin, restoringId, restore, openingFolderId, openFolder }: { item: LibraryItem; isAdmin: boolean; restoringId: string | null; restore: (item: LibraryItem) => void; openingFolderId: string | null; openFolder: (recordId: string, scope: "source" | "version") => void }) {
  const transfer = statusMeta[item.latest_transfer_status] ?? { label: item.latest_transfer_status, className: "" }
  const availability = availabilityMeta[item.local_availability] ?? availabilityMeta.unknown
  return <Card><CardHeader><div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h3 className="font-semibold text-white">{item.display_name || item.repo_id}</h3>{isAdmin && <Button variant="ghost" size="icon" className="size-7" onClick={() => openFolder(item.latest_record_id, "version")} disabled={openingFolderId === item.latest_record_id} aria-label={`開啟 ${item.display_name || item.repo_id} 本機資料夾`} title="在 Windows Explorer 開啟本機資料夾">{openingFolderId === item.latest_record_id ? <LoaderCircle className="size-3.5 animate-spin" /> : <FolderOpen className="size-3.5" />}</Button>}</div><div className="mt-2"><SourceBadge provider={item.provider} repoType={item.repo_type} /></div><div className="mt-1 font-mono text-[10px] text-slate-600">{item.commit_hash.slice(0, 12)}</div></div><div className="flex flex-wrap justify-end gap-2"><Badge className={transfer.className}>Latest: {transfer.label}</Badge><Badge className={availability.className}>Local: {availability.label}</Badge>{item.history_count > 1 && <Badge>{item.history_count} 次傳輸</Badge>}</div></div></CardHeader><CardContent><div className="max-h-56 space-y-1 overflow-auto">{item.files.map((file) => <div key={file.path} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-slate-400"><FileStatus status={file.local_status === "available" ? "completed" : file.local_status} /><span className="min-w-0 flex-1 truncate">{file.path}</span><span className={cn("text-[10px] uppercase", file.local_status === "available" ? "text-emerald-400" : file.local_status === "changed" ? "text-rose-400" : "text-slate-500")}>{file.local_status}</span><span className="font-mono text-slate-600">{formatBytes(file.size)}</span>{file.local_status === "available" && <a href={fileDownloadUrl(file.record_id, file.path)} className="text-cyan-400"><FileDown className="size-3.5" /></a>}</div>)}</div>{isAdmin && item.restore_record_ids.length > 0 && ["moved", "partial"].includes(item.local_availability) && <div className="mt-4 flex justify-end"><Button onClick={() => restore(item)} disabled={restoringId === item.key}>{restoringId === item.key ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}{item.local_availability === "moved" ? "重新下載全部" : "補回缺少檔案"}</Button></div>}</CardContent></Card>
}

function CivitaiLibraryVersionRow({ item, isAdmin, restoringId, restore, openingFolderId, openFolder, selectedTags, onTag, onModelType, onBaseModel, onCreator }: { item: LibraryItem; isAdmin: boolean; restoringId: string | null; restore: (item: LibraryItem) => void; openingFolderId: string | null; openFolder: (recordId: string, scope: "source" | "version") => void; selectedTags: string[]; onTag: (tag: string) => void; onModelType: (value: string) => void; onBaseModel: (value: string) => void; onCreator: (value: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const transfer = statusMeta[item.latest_transfer_status] ?? { label: item.latest_transfer_status, className: "" }
  const availability = availabilityMeta[item.local_availability] ?? availabilityMeta.unknown
  const versionName = metadataText(item.provider_metadata, "version_name") || item.requested_revision || item.commit_hash
  return <div className="bg-[#0d141c]/55">
    <div className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:px-4">
      <button type="button" onClick={() => setExpanded(!expanded)} className="group min-w-0 flex-1 text-left">
        <div className="flex flex-wrap items-center gap-2"><ChevronDown className={cn("size-4 shrink-0 text-slate-600 transition-transform group-hover:text-cyan-300", expanded && "rotate-180")} /><h4 className="truncate font-semibold text-slate-100">{versionName}</h4><Badge className={transfer.className}>{transfer.label}</Badge><Badge className={availability.className}>{availability.label}</Badge></div>
        <div className="mt-1 flex flex-wrap gap-x-3 pl-6 text-[11px] text-slate-600"><span className="font-mono">Version {item.commit_hash}</span><span>{item.files.length} files</span><span>{formatBytes(item.total_bytes)}</span>{item.history_count > 1 && <span>{item.history_count} 次傳輸</span>}</div>
      </button>
      {isAdmin && <div className="flex items-center gap-1.5"><Button variant="ghost" size="icon" onClick={() => openFolder(item.latest_record_id, "version")} disabled={openingFolderId === item.latest_record_id} aria-label={`開啟 ${versionName} 本機資料夾`} title="開啟此版本的本機資料夾">{openingFolderId === item.latest_record_id ? <LoaderCircle className="size-4 animate-spin" /> : <FolderOpen className="size-4" />}</Button>{item.restore_record_ids.length > 0 && ["moved", "partial"].includes(item.local_availability) && <Button size="sm" onClick={() => restore(item)} disabled={restoringId === item.key}>{restoringId === item.key ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}{item.local_availability === "moved" ? "重新下載全部" : "補回缺少檔案"}</Button>}</div>}
    </div>
    {expanded && <div className="border-t border-white/[.055] bg-[#080d13]/65 px-3 py-3 sm:px-4">
      <CivitaiLibraryMetadata item={item} selectedTags={selectedTags} onTag={onTag} onModelType={onModelType} onBaseModel={onBaseModel} onCreator={onCreator} />
      <div className="divide-y divide-white/[.04]">{item.files.map((file) => {
        const comfyuiPath = metadataText(file.provider_metadata, "comfyui_path")
        return <div key={file.path} className="grid grid-cols-[auto_minmax(0,1fr)_1.5rem] items-start gap-2 py-2 text-xs"><div className="pt-0.5"><FileStatus status={file.local_status === "available" ? "completed" : file.local_status} /></div><div className="min-w-0 sm:grid sm:grid-cols-[minmax(0,1fr)_minmax(10rem,auto)_auto_5rem] sm:items-center sm:gap-3"><div className="truncate text-slate-400">{file.path}</div><div className="truncate text-[10px] text-cyan-300/80">{comfyuiPath ? <code>{comfyuiPath}</code> : <span className="text-slate-700">—</span>}</div><span className={cn("text-[10px] uppercase", file.local_status === "available" ? "text-emerald-400" : file.local_status === "changed" ? "text-rose-400" : "text-slate-500")}>{file.local_status}</span><span className="font-mono text-[10px] text-slate-600 sm:text-right">{formatBytes(file.size)}</span></div><div className="flex justify-end pt-0.5">{file.local_status === "available" && <a href={fileDownloadUrl(file.record_id, file.path)} className="text-cyan-400" aria-label={`下載 ${file.path}`}><FileDown className="size-3.5" /></a>}</div></div>
      })}</div>
    </div>}
  </div>
}

type GalleryImage = { file: LibraryFile; versionName: string }

function isGalleryImage(file: LibraryFile): boolean {
  return file.local_status === "available" && /\.(?:avif|gif|jpe?g|png|webp)$/i.test(file.path)
}

function civitaiModelUrl(repoId: string): string | null {
  const modelId = repoId.match(/(?:^|\/)models?\/(\d+)$|^(\d+)$/i)
  const value = modelId?.[1] || modelId?.[2]
  return value ? `https://civitai.com/models/${value}` : null
}

function CivitaiSampleGallery({ images }: { images: GalleryImage[] }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  useEffect(() => {
    if (openIndex === null) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenIndex(null)
      if (event.key === "ArrowLeft") setOpenIndex((current) => current === null ? null : (current - 1 + images.length) % images.length)
      if (event.key === "ArrowRight") setOpenIndex((current) => current === null ? null : (current + 1) % images.length)
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    window.addEventListener("keydown", onKeyDown)
    return () => { window.removeEventListener("keydown", onKeyDown); document.body.style.overflow = previousOverflow }
  }, [images.length, openIndex])
  if (!images.length) return null
  const currentIndex = openIndex ?? 0
  const current = openIndex === null ? null : images[currentIndex]
  const preview = images[0]
  return <>
    <button type="button" onClick={() => setOpenIndex(0)} className="group relative h-16 w-24 shrink-0 overflow-hidden rounded-lg border border-white/10 bg-black/25 sm:h-20 sm:w-28" aria-label={`開啟 ${images.length} 張模型範例圖`}>
      <img src={fileDownloadUrl(preview.file.record_id, preview.file.path)} alt="模型範例預覽" className="size-full object-cover transition duration-300 group-hover:scale-105" />
      <span className="absolute bottom-1.5 right-1.5 flex items-center gap-1 rounded-md bg-black/75 px-1.5 py-1 text-[9px] font-semibold text-white"><Images className="size-3" />{images.length}</span>
    </button>
    {current && <div role="dialog" aria-modal="true" aria-label="模型範例圖庫" className="fixed inset-0 z-[80] flex items-center justify-center bg-black/90 p-3 backdrop-blur-sm sm:p-8" onClick={() => setOpenIndex(null)}>
      <button type="button" onClick={() => setOpenIndex(null)} className="absolute right-4 top-4 z-10 grid size-10 place-items-center rounded-full border border-white/15 bg-black/60 text-white hover:bg-white/10" aria-label="關閉圖片瀏覽"><X className="size-5" /></button>
      {images.length > 1 && <button type="button" onClick={(event) => { event.stopPropagation(); setOpenIndex((currentIndex - 1 + images.length) % images.length) }} className="absolute left-3 z-10 grid size-10 place-items-center rounded-full border border-white/15 bg-black/60 text-white hover:bg-white/10 sm:left-6" aria-label="上一張"><ChevronLeft className="size-6" /></button>}
      <div className="flex max-h-full max-w-6xl flex-col items-center" onClick={(event) => event.stopPropagation()}><img src={fileDownloadUrl(current.file.record_id, current.file.path)} alt={current.file.path} className="max-h-[82vh] max-w-full rounded-lg object-contain shadow-2xl" /><div className="mt-3 flex max-w-full items-center gap-3 text-xs text-slate-300"><span className="truncate">{current.versionName} · {current.file.path}</span><span className="shrink-0 text-slate-500">{currentIndex + 1} / {images.length}</span></div></div>
      {images.length > 1 && <button type="button" onClick={(event) => { event.stopPropagation(); setOpenIndex((currentIndex + 1) % images.length) }} className="absolute right-3 z-10 grid size-10 place-items-center rounded-full border border-white/15 bg-black/60 text-white hover:bg-white/10 sm:right-6" aria-label="下一張"><ChevronRight className="size-6" /></button>}
    </div>}
  </>
}

function CivitaiLibraryGroup({ items, isAdmin, restoringId, restore, openingFolderId, openFolder, selectedTags, onTag, onModelType, onBaseModel, onCreator }: { items: LibraryItem[]; isAdmin: boolean; restoringId: string | null; restore: (item: LibraryItem) => void; openingFolderId: string | null; openFolder: (recordId: string, scope: "source" | "version") => void; selectedTags: string[]; onTag: (tag: string) => void; onModelType: (value: string) => void; onBaseModel: (value: string) => void; onCreator: (value: string) => void }) {
  const totalFiles = new Set(items.flatMap((item) => item.files.map((file) => file.id || file.path))).size
  const totalBytes = items.reduce((sum, item) => sum + item.total_bytes, 0)
  const availableVersions = items.filter((item) => item.local_availability === "available").length
  const hasChanged = items.some((item) => ["changed", "moved", "partial"].includes(item.local_availability))
  const galleryEntries = items.flatMap((item) => item.files.filter(isGalleryImage).map((file): [string, GalleryImage] => [`${file.record_id}:${file.path}`, { file, versionName: metadataText(item.provider_metadata, "version_name") || item.requested_revision || item.commit_hash }]))
  const galleryImages = [...new Map(galleryEntries).values()]
  const sourceUrl = civitaiModelUrl(items[0].repo_id)
  return <Card className="overflow-hidden border-cyan-400/15 bg-cyan-400/[.015] md:col-span-2">
    <div className="flex flex-col gap-3 border-b border-cyan-400/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-3"><CivitaiSampleGallery images={galleryImages} /><div className="min-w-0"><div className="flex items-center gap-2"><Library className="size-5 shrink-0 text-cyan-400" /><h3 className="truncate font-semibold text-white">{items[0].display_name || items[0].repo_id}</h3>{sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer" className="grid size-7 shrink-0 place-items-center rounded-md border border-white/[.07] text-slate-500 transition hover:border-cyan-300/25 hover:bg-cyan-300/10 hover:text-cyan-300" aria-label={`在 Civitai 開啟 ${items[0].display_name || items[0].repo_id}`} title="在 Civitai 開啟原始模型頁"><ExternalLink className="size-3.5" /></a>}{isAdmin && <Button variant="ghost" size="icon" className="size-7" onClick={() => openFolder(items[0].latest_record_id, "source")} disabled={openingFolderId === items[0].latest_record_id} aria-label={`開啟 ${items[0].display_name || items[0].repo_id} 本機資料夾`} title="開啟此模型的本機資料夾">{openingFolderId === items[0].latest_record_id ? <LoaderCircle className="size-3.5 animate-spin" /> : <FolderOpen className="size-3.5" />}</Button>}</div><div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500"><span>Civitai · Model {items[0].repo_id}</span><span aria-hidden="true">·</span><span>{items.length} 個版本</span><span aria-hidden="true">·</span><span>{totalFiles} 個檔案</span><span aria-hidden="true">·</span><span>{formatBytes(totalBytes)}</span></div></div></div>
      <div className="flex shrink-0 items-center gap-2 self-start text-xs sm:self-auto"><span className={cn("size-2 rounded-full", hasChanged ? "bg-amber-400" : availableVersions === items.length ? "bg-emerald-400" : "bg-slate-500")} /><span className="text-slate-400">{hasChanged ? "本機內容需留意" : availableVersions === items.length ? "所有版本可用" : `${availableVersions} / ${items.length} 可用`}</span></div>
    </div>
    <div className="divide-y divide-white/[.055]">{items.map((item) => <CivitaiLibraryVersionRow key={item.key} item={item} isAdmin={isAdmin} restoringId={restoringId} restore={restore} openingFolderId={openingFolderId} openFolder={openFolder} selectedTags={selectedTags} onTag={onTag} onModelType={onModelType} onBaseModel={onBaseModel} onCreator={onCreator} />)}</div>
  </Card>
}

function LibraryPage({ items, isAdmin, refresh }: { items: LibraryItem[]; isAdmin: boolean; refresh: () => Promise<void> }) {
  const [category, setCategory] = useStoredState<SourceCategory>("hfdm.library.category", "hf-model")
  const [query, setQuery] = useStoredState("hfdm.library.query", "")
  const [modelType, setModelType] = useStoredState("hfdm.library.model-type", "")
  const [baseModel, setBaseModel] = useStoredState("hfdm.library.base-model", "")
  const [creator, setCreator] = useStoredState("hfdm.library.creator", "")
  const [storedTags, setStoredTags] = useStoredState<string[] | string>("hfdm.library.tag", [])
  const [comfyuiFolder, setComfyuiFolder] = useStoredState("hfdm.library.comfyui-folder", "")
  const [scanning, setScanning] = useState(false)
  const [restoringId, setRestoringId] = useState<string | null>(null)
  const [openingFolderId, setOpeningFolderId] = useState<string | null>(null)
  const [error, setError] = useState("")
  const selectedTags = Array.isArray(storedTags) ? storedTags : storedTags ? [storedTags] : []
  const toggleTag = (value: string) => setStoredTags(selectedTags.includes(value) ? selectedTags.filter((tag) => tag !== value) : [...selectedTags, value])
  const rescan = async () => {
    setScanning(true)
    try { await api.reconcileHistory(); await refresh() }
    finally { setScanning(false) }
  }
  const restore = async (item: LibraryItem) => {
    let token = ""
    if (item.requires_token) {
      token = window.prompt(`此下載需要 ${item.provider === "civitai" ? "Civitai API" : "Hugging Face"} Token。Token 只會保留在記憶體中。`) ?? ""
      if (!token) return
    }
    setRestoringId(item.key); setError("")
    try {
      for (const recordId of item.restore_record_ids) await api.redownloadMissing(recordId, item.provider, token)
      await refresh()
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "重新下載失敗") }
    finally { setRestoringId(null) }
  }
  const openFolder = async (recordId: string, scope: "source" | "version") => {
    setOpeningFolderId(recordId); setError("")
    try { await api.openLibraryFolder(recordId, scope) }
    catch (reason) { setError(reason instanceof Error ? reason.message : "無法開啟本機資料夾") }
    finally { setOpeningFolderId(null) }
  }
  const counts = useMemo(() => ({
    "hf-model": items.filter((item) => sourceCategory(item.provider, item.repo_type) === "hf-model").length,
    "hf-dataset": items.filter((item) => sourceCategory(item.provider, item.repo_type) === "hf-dataset").length,
    "civitai-model": new Set(items.filter((item) => sourceCategory(item.provider, item.repo_type) === "civitai-model").map((item) => item.repo_id)).size,
  }), [items])
  const civitaiItems = useMemo(() => items.filter((item) => sourceCategory(item.provider, item.repo_type) === "civitai-model"), [items])
  const filterOptions = useMemo(() => ({
    modelTypes: [...new Set(civitaiItems.map((item) => metadataText(item.provider_metadata, "model_type")).filter(Boolean))].sort(),
    baseModels: [...new Set(civitaiItems.map((item) => metadataText(item.provider_metadata, "base_model")).filter(Boolean))].sort(),
    creators: [...new Set(civitaiItems.map((item) => metadataText(item.provider_metadata, "creator")).filter(Boolean))].sort(),
    tags: [...new Set(civitaiItems.flatMap((item) => metadataList(item.provider_metadata, "tags")))].sort(),
    folders: [...new Set(civitaiItems.flatMap((item) => item.files.map((file) => metadataText(file.provider_metadata, "comfyui_folder")).filter(Boolean)))].sort(),
  }), [civitaiItems])
  const visible = items.filter((item) => {
    if (sourceCategory(item.provider, item.repo_type) !== category) return false
    const metadata = item.provider_metadata
    const tags = metadataList(metadata, "tags")
    const folders = item.files.map((file) => metadataText(file.provider_metadata, "comfyui_folder")).filter(Boolean)
    const searchable = [item.display_name, item.repo_id, metadataText(metadata, "creator"), metadataText(metadata, "model_type"), metadataText(metadata, "base_model"), ...tags, ...folders].filter(Boolean).join(" ").toLocaleLowerCase()
    const normalizedQuery = query.trim().toLocaleLowerCase()
    if (normalizedQuery && !searchable.includes(normalizedQuery)) return false
    if (category === "civitai-model") {
      if (modelType && metadataText(metadata, "model_type") !== modelType) return false
      if (baseModel && metadataText(metadata, "base_model") !== baseModel) return false
      if (creator && metadataText(metadata, "creator") !== creator) return false
      if (selectedTags.some((tag) => !tags.includes(tag))) return false
      if (comfyuiFolder && !folders.includes(comfyuiFolder)) return false
    }
    return true
  })
  const regularItems = visible.filter((item) => item.provider !== "civitai")
  const civitaiGroups = [...new Map(visible.filter((item) => item.provider === "civitai").map((item) => [item.repo_id, visible.filter((candidate) => candidate.provider === "civitai" && candidate.repo_id === item.repo_id)])).values()]
  return <><PageHeading eyebrow="Content library" title="內容庫與本機狀態" description="Hugging Face Model、Hugging Face Dataset 與 Civitai Model 分類管理；同一來源、版本與下載位置只顯示一次。" action={isAdmin ? <Button variant="secondary" onClick={() => void rescan()} disabled={scanning}>{scanning ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}重新掃描 download/</Button> : undefined} />
    <CategoryTabs value={category} onChange={(value) => value !== "all" && setCategory(value)} counts={counts} />
    <div className="mb-5 rounded-xl border border-white/[.06] bg-white/[.02] p-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-600" />
        <Input className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋名稱、作者、模型類型、base model、標籤或 ComfyUI 目錄" />
      </div>
      {category === "civitai-model" && <><div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
        {[
          [modelType, setModelType, "全部模型類型", filterOptions.modelTypes],
          [baseModel, setBaseModel, "全部 Base Model", filterOptions.baseModels],
          [creator, setCreator, "全部 Creator", filterOptions.creators],
          [comfyuiFolder, setComfyuiFolder, "全部 ComfyUI 目錄", filterOptions.folders],
        ].map(([value, setter, label, options]) => <select key={String(label)} value={String(value)} onChange={(event) => (setter as (value: string) => void)(event.target.value)} className="h-9 rounded-md border border-white/10 bg-[#0c131b] px-3 text-xs text-slate-300 outline-none focus:border-cyan-400/40">
          <option value="">{String(label)}</option>
          {(options as string[]).map((option) => <option key={option} value={option}>{option}</option>)}
        </select>)}
        <select aria-label="加入標籤篩選" value="" onChange={(event) => { if (event.target.value) toggleTag(event.target.value) }} className="h-9 rounded-md border border-white/10 bg-[#0c131b] px-3 text-xs text-slate-300 outline-none focus:border-cyan-400/40"><option value="">加入標籤篩選…</option>{filterOptions.tags.filter((tag) => !selectedTags.includes(tag)).map((tag) => <option key={tag} value={tag}>#{tag}</option>)}</select>
        <Button variant="secondary" onClick={() => { setQuery(""); setModelType(""); setBaseModel(""); setCreator(""); setStoredTags([]); setComfyuiFolder("") }}>清除篩選</Button>
      </div>{selectedTags.length > 0 && <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500"><span>符合全部標籤：</span>{selectedTags.map((tag) => <button key={tag} type="button" onClick={() => toggleTag(tag)} className="flex items-center gap-1 rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2 py-1 text-cyan-200">#{tag}<X className="size-3" /></button>)}</div>}</>}
    </div>
    {error && <div className="mb-4 flex items-start gap-2 rounded-lg border border-rose-400/15 bg-rose-400/[.07] p-3 text-sm text-rose-300"><XCircle className="mt-0.5 size-4 shrink-0" />{error}</div>}
    <div className="grid gap-4 md:grid-cols-2">{visible.length ? <>{regularItems.map((item) => <LibraryVersionCard key={item.key} item={item} isAdmin={isAdmin} restoringId={restoringId} restore={(target) => void restore(target)} openingFolderId={openingFolderId} openFolder={(recordId, scope) => void openFolder(recordId, scope)} />)}{civitaiGroups.map((group) => <CivitaiLibraryGroup key={group[0].repo_id} items={group} isAdmin={isAdmin} restoringId={restoringId} restore={(target) => void restore(target)} openingFolderId={openingFolderId} openFolder={(recordId, scope) => void openFolder(recordId, scope)} selectedTags={selectedTags} onTag={toggleTag} onModelType={(value) => setModelType(modelType === value ? "" : value)} onBaseModel={(value) => setBaseModel(baseModel === value ? "" : value)} onCreator={(value) => setCreator(creator === value ? "" : value)} />)}</> : <div className="md:col-span-2"><EmptyState icon={Library} title={`${categoryLabels[category]} 內容庫尚無項目`} text="完成此分類的下載後，實體內容會聚合顯示在這裡。" /></div>}</div>
  </>
}

function SettingsPage({ isAdmin }: { isAdmin: boolean }) {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => { void api.settings().then(setSettings).catch((reason) => setError(String(reason))) }, [])
  const save = async () => {
    if (!settings) return
    setError("")
    try { setSettings(await api.updateSettings(settings)); setSaved(true); window.setTimeout(() => setSaved(false), 1800) }
    catch (reason) { setError(reason instanceof Error ? reason.message : "儲存失敗") }
  }
  return <><PageHeading eyebrow="Server controls" title="服務設定" description="容量與保留政策只影響後續任務。管理寫入僅允許從伺服器本機連線。" />
    {!isAdmin && <Card className="mb-5 border-amber-400/15 bg-amber-400/[.04]"><CardContent className="flex items-center gap-3 p-4 text-sm text-amber-200"><ShieldCheck className="size-5" />目前是訪客連線；設定為唯讀。</CardContent></Card>}
    {settings && <Card className="max-w-2xl"><CardHeader><h2 className="font-semibold text-white">下載與儲存</h2></CardHeader><CardContent className="space-y-5">
      <SettingField label="Hugging Face 效能模式" note="Balanced 為 benchmark 建議預設；Maximum 提高資源占用，HDD 偏好順序寫入"><select value={settings.hf_profile} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, hf_profile: e.target.value as AppSettings["hf_profile"] })} className="h-9 rounded-md border border-white/10 bg-[#0c131b] px-3 text-sm text-slate-300 outline-none focus:border-cyan-400/40 disabled:opacity-50"><option value="balanced">Balanced（建議）</option><option value="maximum">Maximum</option><option value="hdd">HDD 順序寫入</option></select></SettingField>
      <SettingField label="同時下載檔案數" note="全服務共用的 worker 上限"><Input type="number" min={1} max={16} value={settings.max_concurrent_files} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, max_concurrent_files: Number(e.target.value) })} /></SettingField>
      <SettingField label="Civitai 分段數" note="支援 Range 時每個檔案使用 1–8 段；不支援時自動退回單串流"><Input type="number" min={1} max={8} value={settings.civitai_segments} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, civitai_segments: Number(e.target.value) })} /></SettingField>
      <SettingField label="容量上限（GiB）" note="0 代表不限容量"><Input type="number" min={0} value={Math.round(settings.max_storage_bytes / 1024 ** 3)} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, max_storage_bytes: Number(e.target.value) * 1024 ** 3 })} /></SettingField>
      <SettingField label="磁碟安全保留（GiB）" note="低於此空間時拒絕新任務"><Input type="number" min={0} value={Math.round(settings.min_free_bytes / 1024 ** 3)} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, min_free_bytes: Number(e.target.value) * 1024 ** 3 })} /></SettingField>
      <SettingField label="保留天數" note="0 代表不自動過期；自動清理將於後續版本啟用"><Input type="number" min={0} value={settings.retention_days} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, retention_days: Number(e.target.value) })} /></SettingField>
      <SettingField label="允許刪除實體檔案" note="關閉後只能保留共用 Model／Dataset 內容"><button type="button" disabled={!isAdmin} onClick={() => setSettings({ ...settings, allow_delete_files: !settings.allow_delete_files })} className={cn("relative h-7 w-12 rounded-full border transition", settings.allow_delete_files ? "border-cyan-400/40 bg-cyan-400/25" : "border-white/10 bg-white/[.04]")}><span className={cn("absolute top-1 size-4 rounded-full bg-white transition-all", settings.allow_delete_files ? "left-7" : "left-1")} /></button></SettingField>
      {error && <div className="text-xs text-rose-300">{error}</div>}
      {isAdmin && <div className="flex justify-end"><Button onClick={() => void save()}>{saved ? <CheckCircle2 className="size-4" /> : <SettingsIcon className="size-4" />}{saved ? "已儲存" : "儲存設定"}</Button></div>}
    </CardContent></Card>}
  </>
}

function SettingField({ label, note, children }: { label: string; note: string; children: React.ReactNode }) {
  return <label className="grid items-center gap-3 sm:grid-cols-[1fr_180px]"><div><div className="text-sm font-medium text-slate-300">{label}</div><div className="mt-1 text-xs text-slate-600">{note}</div></div>{children}</label>
}

function EmptyState({ icon: Icon, title, text }: { icon: typeof Activity; title: string; text: string }) {
  return <Card><CardContent className="flex min-h-56 flex-col items-center justify-center p-8 text-center"><div className="mb-4 grid size-12 place-items-center rounded-2xl border border-white/[.07] bg-white/[.025]"><Icon className="size-5 text-slate-600" /></div><div className="font-medium text-slate-300">{title}</div><p className="mt-2 text-sm text-slate-600">{text}</p></CardContent></Card>
}

export default App
