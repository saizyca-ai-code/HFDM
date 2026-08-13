import {
  Activity,
  Archive,
  Box,
  CheckCircle2,
  ChevronDown,
  CircleGauge,
  Download,
  FileDown,
  HardDrive,
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
  XCircle,
  Zap,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"

import { FileTree } from "@/components/file-tree"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { api, fileDownloadUrl, type AppSettings, type DownloadTask, type LibraryItem, type RepoResolution, type TaskInspection } from "@/lib/api"
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
  return <Badge className={category === "civitai-model" ? "border-cyan-400/20 bg-cyan-400/10 text-cyan-300" : category === "hf-dataset" ? "border-violet-400/20 bg-violet-400/10 text-violet-300" : "border-sky-400/20 bg-sky-400/10 text-sky-300"}>{categoryLabels[category]}</Badge>
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
  const [page, setPage] = useState<Page>("huggingface")
  const [hfDraft, setHfDraft] = useState<NewDownloadDraft>(() => ({
    ...emptyDownloadDraft(),
    source: "https://huggingface.co/Comfy-Org/z_image_turbo/tree/main",
  }))
  const [civitaiDraft, setCivitaiDraft] = useState<NewDownloadDraft>(emptyDownloadDraft)
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
          {page === "huggingface" && <NewDownload provider="huggingface" draft={hfDraft} setDraft={setHfDraft} onCreated={() => { setHfDraft(emptyDownloadDraft()); void refreshTasks(); setPage("transfers") }} />}
          {page === "civitai" && <NewDownload provider="civitai" draft={civitaiDraft} setDraft={setCivitaiDraft} onCreated={() => { setCivitaiDraft(emptyDownloadDraft()); void refreshTasks(); setPage("transfers") }} />}
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

function NewDownload({ provider, draft, setDraft, onCreated }: { provider: DownloadProvider; draft: NewDownloadDraft; setDraft: React.Dispatch<React.SetStateAction<NewDownloadDraft>>; onCreated: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const { source, token, includeGlobs, excludeGlobs, versionId, repo, selected } = draft
  const isCivitai = provider === "civitai"
  const selectedBytes = useMemo(() => repo?.files.filter((file) => selected.has(file.path)).reduce((sum, file) => sum + file.size, 0) ?? 0, [repo, selected])

  const updateDraft = (values: Partial<NewDownloadDraft>) => {
    setDraft((current) => ({ ...current, ...values }))
  }
  const clearDraft = () => {
    setDraft(emptyDownloadDraft())
    setError("")
  }

  const resolve = async (nextVersionId?: number) => {
    setBusy(true); setError("")
    try {
      const result = await api.resolveRepo(
        source,
        token,
        splitGlobInput(includeGlobs),
        splitGlobInput(excludeGlobs),
        nextVersionId,
      )
      if (result.provider !== provider) throw new Error(`此入口只接受 ${isCivitai ? "Civitai model URL" : "Hugging Face Model／Dataset URL"}`)
      updateDraft({ repo: result, versionId: result.provider === "civitai" ? result.commit_hash : "", selected: new Set(result.suggested_files) })
    } catch (reason) { setError(reason instanceof Error ? reason.message : "讀取 repo 失敗") }
    finally { setBusy(false) }
  }
  const create = async () => {
    if (!repo || !selected.size) return
    setBusy(true); setError("")
    try {
      await api.createTask(source, [...selected], token, repo.provider === "civitai" ? Number(versionId) : undefined)
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
          {repo && <div className="space-y-4 pt-2">
            <div className="flex flex-wrap items-center gap-2"><Badge className="border-cyan-400/20 bg-cyan-400/10 text-cyan-300">{repo.display_name || repo.repo_id}</Badge><Badge>{repo.provider === "civitai" ? "Civitai" : "Hugging Face"}</Badge><Badge className={repo.repo_type === "dataset" ? "border-violet-400/20 bg-violet-400/10 text-violet-300" : ""}>{repo.repo_type === "dataset" ? "Dataset" : "Model"}</Badge><Badge>{repo.version_name || repo.requested_revision}</Badge><span className="font-mono text-[10px] text-slate-600">{repo.commit_hash.slice(0, 12)}</span></div>
            {repo.provider === "civitai" && repo.versions.length > 0 && <label className="block rounded-lg border border-white/[.07] bg-white/[.025] p-3 text-xs text-slate-400"><span className="mb-1 block font-medium text-slate-300">模型系列／版本</span><span className="mb-3 block text-[11px] text-slate-600">切換後會重新列出該版本實際提供的精度與格式檔案。</span><select aria-label="模型系列／版本" className="h-10 w-full rounded-lg border border-white/10 bg-[#0a1016] px-3 text-sm text-slate-200" value={versionId} onChange={(event) => { const next = event.target.value; updateDraft({ versionId: next }); void resolve(Number(next)) }}>{repo.versions.map((version) => <option key={version.id} value={version.id}>{version.name}{version.base_model ? ` · ${version.base_model}` : ""}</option>)}</select></label>}
            {repo.provider === "civitai" && <div className="rounded-lg border border-cyan-400/15 bg-cyan-400/[.04] p-3 text-xs leading-5 text-slate-400">{String(repo.provider_metadata.model_type || "Model")}{repo.provider_metadata.base_model ? ` · ${String(repo.provider_metadata.base_model)}` : ""}{repo.provider_metadata.creator ? ` · by ${String(repo.provider_metadata.creator)}` : ""}。完成時會驗證 Civitai 提供的 SHA256。</div>}
            {repo.repo_type === "dataset" && repo.files.length >= 100 && <div className="rounded-lg border border-amber-400/15 bg-amber-400/[.05] p-3 text-xs leading-5 text-amber-200">此 Dataset 含有 {repo.files.length} 個檔案。大量小檔會增加排程與磁碟負擔；可使用 glob 縮小範圍，並在「服務設定」調整同時下載檔案數。</div>}
            {repo.provider === "civitai" && <div className="text-xs font-medium text-slate-300">此版本的檔案變體與附加內容</div>}
            <FileTree files={repo.files} selected={selected} onChange={(next) => updateDraft({ selected: next })} />
            <div className="flex flex-col items-start justify-between gap-3 rounded-xl border border-white/[.07] bg-white/[.025] p-4 sm:flex-row sm:items-center"><div><div className="text-sm font-medium text-slate-200">已選 {selected.size} / {repo.files.length} 個檔案</div><div className="mt-1 text-xs text-slate-500">合計 {formatBytes(selectedBytes)} · 儲存於 download/{repo.provider === "civitai" ? "civitai/models" : repo.repo_type === "dataset" ? "datasets" : "models"}/</div></div><Button onClick={() => void create()} disabled={busy || !selected.size}><Play className="size-4 fill-current" />建立下載任務</Button></div>
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

function Transfers({ tasks, isAdmin, refresh }: { tasks: DownloadTask[]; isAdmin: boolean; refresh: () => Promise<void> }) {
  const [category, setCategory] = useState<SourceCategory | "all">("all")
  const active = tasks.filter((task) => task.status !== "completed" && task.status !== "cancelled")
  const counts = useMemo(() => ({
    "hf-model": tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === "hf-model").length,
    "hf-dataset": tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === "hf-dataset").length,
    "civitai-model": tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === "civitai-model").length,
  }), [tasks])
  const visible = category === "all" ? tasks : tasks.filter((task) => sourceCategory(task.provider, task.repo_type) === category)
  return <><PageHeading eyebrow="Transfers" title="下載任務" description="即時查看服務端取得進度。暫停會停止排程並終止正在執行的檔案 worker。" action={<div className="flex items-center gap-2 text-xs text-slate-500"><CircleGauge className="size-4 text-cyan-400" />{active.length} active</div>} />
    <CategoryTabs value={category} onChange={setCategory} counts={counts} includeAll />
    <div className="space-y-4">{visible.length ? visible.map((task) => <TaskCard key={task.id} task={task} isAdmin={isAdmin} refresh={refresh} />) : <EmptyState icon={Activity} title="此分類尚無下載任務" text="請從左側選擇 Hugging Face 或 Civitai 下載入口建立任務。" />}</div>
  </>
}

function TaskCard({ task, isAdmin, refresh }: { task: DownloadTask; isAdmin: boolean; refresh: () => Promise<void> }) {
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
    setEditing(true); setInspection(null); setEditSelected(new Set()); setEditMessage(""); setError("")
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
  return <Card className="overflow-hidden">
    <div className="p-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-center">
        <div className="grid size-11 shrink-0 place-items-center rounded-xl border border-white/[.07] bg-[#0a1016]"><Box className="size-5 text-cyan-400/75" /></div>
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="truncate font-semibold text-slate-100">{task.display_name || task.repo_id}</h3><SourceBadge provider={task.provider} repoType={task.repo_type} /><Badge className={meta.className}>Transfer: {meta.label}</Badge><Badge className={availability.className}>Local: {availability.label}</Badge></div><div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-600"><span>{task.requested_revision}</span><span className="font-mono">{task.commit_hash.slice(0, 10)}</span><span>{task.files.length} files</span></div></div>
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
      <div className="mt-5"><div className="mb-2 flex flex-wrap justify-between gap-2 text-xs"><span className="text-slate-500">{formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}</span><span className="flex gap-3 font-mono text-slate-500"><span>{task.status === "downloading" ? `${formatBytes(task.speed_bps)}/s` : "— B/s"}</span><span>ETA {task.status === "downloading" ? formatEta(task.eta_seconds) : "—"}</span><span className="text-slate-300">{progress}%</span></span></div><Progress value={progress} /></div>
      {(error || task.error) && <div className="mt-3 text-xs text-rose-300">{error || task.error}</div>}
      {editMessage && <div className="mt-3 text-xs text-emerald-300">{editMessage}</div>}
      {editing && <div className="mt-4 space-y-4 rounded-xl border border-cyan-400/15 bg-cyan-400/[.035] p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><div className="text-sm font-semibold text-slate-200">編輯下載任務</div><div className="mt-1 text-xs text-slate-500">重新解析 {task.repo_id} / {task.requested_revision}，檢查遠端 commit 與完整檔案樹。</div></div><Button variant="ghost" size="sm" onClick={() => { setEditing(false); setEditToken("") }}>關閉</Button></div>
        <div className="flex flex-col gap-2 sm:flex-row"><div className="relative min-w-0 flex-1"><KeyRound className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-600" /><Input className="pl-10" type="password" autoComplete="off" value={editToken} onChange={(event) => setEditToken(event.target.value)} placeholder={task.requires_token ? `輸入 ${task.provider === "civitai" ? "Civitai API" : "HF"} Token 後重新檢查` : `${task.provider === "civitai" ? "Civitai API" : "HF"} Token（選填，只存於記憶體）`} /></div><Button variant="secondary" onClick={() => void inspectRepo()} disabled={busy || (task.requires_token && !editToken)}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}檢查來源</Button></div>
        {inspection && <>{inspection.resolution.provider === "civitai" && inspection.resolution.versions.length > 0 && <select className="h-10 w-full rounded-lg border border-white/10 bg-[#0a1016] px-3 text-sm text-slate-200" value={inspection.resolution.commit_hash} onChange={(event) => void inspectRepo(Number(event.target.value))}>{inspection.resolution.versions.map((version) => <option key={version.id} value={version.id}>{version.name}{version.base_model ? ` · ${version.base_model}` : ""}</option>)}</select>}<div className="flex flex-wrap items-center gap-2 text-xs"><Badge>{inspection.resolution.version_name || inspection.resolution.requested_revision}</Badge><span className="font-mono text-slate-500">目前 {task.commit_hash.slice(0, 12)}</span><span className="text-slate-600">→</span><span className="font-mono text-slate-300">遠端 {inspection.resolution.commit_hash.slice(0, 12)}</span>{inspection.update_available ? <Badge className="border-amber-400/20 bg-amber-400/10 text-amber-300">來源有更新</Badge> : <Badge className="border-emerald-400/20 bg-emerald-400/10 text-emerald-300">已是相同版本</Badge>}</div>{inspection.unavailable_selected_files.length > 0 && <div className="rounded-lg border border-amber-400/15 bg-amber-400/[.06] p-3 text-xs text-amber-200">原任務有 {inspection.unavailable_selected_files.length} 個檔案已不在目前來源：{inspection.unavailable_selected_files.join(", ")}</div>}<FileTree files={inspection.resolution.files} selected={editSelected} onChange={setEditSelected} /><div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div className="text-xs text-slate-500">已選 {editSelected.size} / {inspection.resolution.files.length} 個檔案。{["downloading", "pausing"].includes(task.status) ? "請先暫停目前下載，再修改設定。" : inspection.can_update_in_place ? "將更新目前任務。" : "儲存時會建立新任務並取代原任務；舊實體檔案不會刪除。"}</div><Button onClick={() => void saveConfiguration()} disabled={busy || !editSelected.size || ["downloading", "pausing"].includes(task.status)}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}{["downloading", "pausing"].includes(task.status) ? "請先暫停" : inspection.can_update_in_place ? "儲存設定" : "建立更新任務"}</Button></div></>}
      </div>}
      <button onClick={() => setExpanded(!expanded)} className="mt-4 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300"><ChevronDown className={cn("size-3.5 transition-transform", expanded && "rotate-180")} />{expanded ? "收合檔案" : "查看檔案"}</button>
    </div>
    {expanded && <div className="max-h-72 overflow-auto border-t border-white/[.06] bg-[#0a1016]/70">{task.files.map((file) => <div key={file.id} className="flex items-center gap-3 border-b border-white/[.035] px-5 py-2.5 text-xs"><FileStatus status={file.status} /><span className="min-w-0 flex-1 truncate text-slate-400">{file.path}</span><Badge className={availabilityMeta[file.local_status]?.className}>{availabilityMeta[file.local_status]?.label ?? file.local_status}</Badge><span className="font-mono text-slate-600">{formatBytes(file.size)}</span>{file.status === "completed" && file.local_status === "available" && <a className="text-cyan-400 hover:text-cyan-300" href={fileDownloadUrl(task.id, file.path)}><FileDown className="size-4" /></a>}</div>)}</div>}
  </Card>
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

function CivitaiLibraryMetadata({ item, onTag }: { item: LibraryItem; onTag: (tag: string) => void }) {
  const tags = metadataList(item.provider_metadata, "tags")
  const modelType = metadataText(item.provider_metadata, "model_type")
  const baseModel = metadataText(item.provider_metadata, "base_model")
  const baseModelType = metadataText(item.provider_metadata, "base_model_type")
  const creator = metadataText(item.provider_metadata, "creator")
  const destinations = item.files.flatMap((file) => {
    const path = metadataText(file.provider_metadata, "comfyui_path")
    return path ? [{ file: file.path, path }] : []
  })
  return <div className="mb-3 space-y-2 rounded-lg border border-cyan-400/10 bg-cyan-400/[.025] p-3 text-xs">
    <div className="flex flex-wrap gap-1.5">{modelType && <Badge className="border-cyan-400/20 bg-cyan-400/10 text-cyan-300">{modelType}</Badge>}{baseModel && <Badge>Base: {baseModel}{baseModelType ? ` · ${baseModelType}` : ""}</Badge>}{creator && <Badge>by {creator}</Badge>}</div>
    {tags.length > 0 && <div className="flex flex-wrap gap-1.5">{tags.map((tag) => <button key={tag} type="button" onClick={() => onTag(tag)} className="rounded-full border border-white/[.07] bg-white/[.035] px-2 py-0.5 text-[10px] text-slate-400 hover:border-cyan-400/30 hover:text-cyan-300">#{tag}</button>)}</div>}
    {destinations.length > 0 && <div className="space-y-1 border-t border-white/[.05] pt-2"><div className="text-[10px] uppercase tracking-wider text-slate-600">ComfyUI 建議放置位置</div>{destinations.map((destination) => <div key={`${destination.file}-${destination.path}`} className="grid gap-1 sm:grid-cols-[minmax(0,1fr)_auto]"><span className="truncate text-slate-500">{destination.file}</span><code className="text-cyan-300">{destination.path}</code></div>)}</div>}
  </div>
}

function LibraryPage({ items, isAdmin, refresh }: { items: LibraryItem[]; isAdmin: boolean; refresh: () => Promise<void> }) {
  const [category, setCategory] = useState<SourceCategory>("hf-model")
  const [query, setQuery] = useState("")
  const [modelType, setModelType] = useState("")
  const [baseModel, setBaseModel] = useState("")
  const [tag, setTag] = useState("")
  const [comfyuiFolder, setComfyuiFolder] = useState("")
  const [scanning, setScanning] = useState(false)
  const [restoringId, setRestoringId] = useState<string | null>(null)
  const [error, setError] = useState("")
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
  const counts = useMemo(() => ({
    "hf-model": items.filter((item) => sourceCategory(item.provider, item.repo_type) === "hf-model").length,
    "hf-dataset": items.filter((item) => sourceCategory(item.provider, item.repo_type) === "hf-dataset").length,
    "civitai-model": items.filter((item) => sourceCategory(item.provider, item.repo_type) === "civitai-model").length,
  }), [items])
  const civitaiItems = useMemo(() => items.filter((item) => sourceCategory(item.provider, item.repo_type) === "civitai-model"), [items])
  const filterOptions = useMemo(() => ({
    modelTypes: [...new Set(civitaiItems.map((item) => metadataText(item.provider_metadata, "model_type")).filter(Boolean))].sort(),
    baseModels: [...new Set(civitaiItems.map((item) => metadataText(item.provider_metadata, "base_model")).filter(Boolean))].sort(),
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
      if (tag && !tags.includes(tag)) return false
      if (comfyuiFolder && !folders.includes(comfyuiFolder)) return false
    }
    return true
  })
  return <><PageHeading eyebrow="Content library" title="內容庫與本機狀態" description="Hugging Face Model、Hugging Face Dataset 與 Civitai Model 分類管理；同一來源、版本與下載位置只顯示一次。" action={isAdmin ? <Button variant="secondary" onClick={() => void rescan()} disabled={scanning}>{scanning ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}重新掃描 download/</Button> : undefined} />
    <CategoryTabs value={category} onChange={(value) => value !== "all" && setCategory(value)} counts={counts} />
    <div className="mb-5 rounded-xl border border-white/[.06] bg-white/[.02] p-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-600" />
        <Input className="pl-9" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋名稱、作者、模型類型、base model、標籤或 ComfyUI 目錄" />
      </div>
      {category === "civitai-model" && <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
        {[
          [modelType, setModelType, "全部模型類型", filterOptions.modelTypes],
          [baseModel, setBaseModel, "全部 Base Model", filterOptions.baseModels],
          [tag, setTag, "全部標籤", filterOptions.tags],
          [comfyuiFolder, setComfyuiFolder, "全部 ComfyUI 目錄", filterOptions.folders],
        ].map(([value, setter, label, options]) => <select key={String(label)} value={String(value)} onChange={(event) => (setter as (value: string) => void)(event.target.value)} className="h-9 rounded-md border border-white/10 bg-[#0c131b] px-3 text-xs text-slate-300 outline-none focus:border-cyan-400/40">
          <option value="">{String(label)}</option>
          {(options as string[]).map((option) => <option key={option} value={option}>{option}</option>)}
        </select>)}
        <Button variant="secondary" onClick={() => { setQuery(""); setModelType(""); setBaseModel(""); setTag(""); setComfyuiFolder("") }}>清除篩選</Button>
      </div>}
    </div>
    {error && <div className="mb-4 flex items-start gap-2 rounded-lg border border-rose-400/15 bg-rose-400/[.07] p-3 text-sm text-rose-300"><XCircle className="mt-0.5 size-4 shrink-0" />{error}</div>}
    <div className="grid gap-4 md:grid-cols-2">{visible.length ? visible.map((item) => {
      const transfer = statusMeta[item.latest_transfer_status] ?? { label: item.latest_transfer_status, className: "" }
      const availability = availabilityMeta[item.local_availability] ?? availabilityMeta.unknown
      return <Card key={item.key}><CardHeader><div className="flex items-start justify-between gap-3"><div><h3 className="font-semibold text-white">{item.display_name || item.repo_id}</h3><div className="mt-2"><SourceBadge provider={item.provider} repoType={item.repo_type} /></div><div className="mt-1 font-mono text-[10px] text-slate-600">{item.commit_hash.slice(0, 12)}</div></div><div className="flex flex-wrap justify-end gap-2"><Badge className={transfer.className}>Latest: {transfer.label}</Badge><Badge className={availability.className}>Local: {availability.label}</Badge>{item.history_count > 1 && <Badge>{item.history_count} 次傳輸</Badge>}</div></div></CardHeader><CardContent>{item.provider === "civitai" && <CivitaiLibraryMetadata item={item} onTag={setTag} />}<div className="max-h-56 space-y-1 overflow-auto">{item.files.map((file) => <div key={file.path} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-slate-400"><FileStatus status={file.local_status === "available" ? "completed" : file.local_status} /><span className="min-w-0 flex-1 truncate">{file.path}</span><span className={cn("text-[10px] uppercase", file.local_status === "available" ? "text-emerald-400" : file.local_status === "changed" ? "text-rose-400" : "text-slate-500")}>{file.local_status}</span><span className="font-mono text-slate-600">{formatBytes(file.size)}</span>{file.local_status === "available" && <a href={fileDownloadUrl(file.record_id, file.path)} className="text-cyan-400"><FileDown className="size-3.5" /></a>}</div>)}</div>{isAdmin && item.restore_record_ids.length > 0 && ["moved", "partial"].includes(item.local_availability) && <div className="mt-4 flex justify-end"><Button onClick={() => void restore(item)} disabled={restoringId === item.key}>{restoringId === item.key ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}{item.local_availability === "moved" ? "重新下載全部" : "補回缺少檔案"}</Button></div>}</CardContent></Card>
    }) : <div className="md:col-span-2"><EmptyState icon={Library} title={`${categoryLabels[category]} 內容庫尚無項目`} text="完成此分類的下載後，實體內容會聚合顯示在這裡。" /></div>}</div>
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
