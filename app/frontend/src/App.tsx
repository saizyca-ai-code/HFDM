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
import { api, fileDownloadUrl, type AppSettings, type DownloadTask, type RepoResolution } from "@/lib/api"
import { cn, formatBytes, formatEta, percent } from "@/lib/utils"

type Page = "new" | "transfers" | "library" | "settings"

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

function App() {
  const [page, setPage] = useState<Page>("new")
  const [tasks, setTasks] = useState<DownloadTask[]>([])
  const [isAdmin, setIsAdmin] = useState(false)
  const [online, setOnline] = useState(false)

  const refreshTasks = useCallback(async () => {
    try {
      setTasks(await api.tasks())
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
          <NavButton active={page === "new"} icon={Plus} label="新增下載" onClick={() => setPage("new")} />
          <NavButton active={page === "transfers"} icon={Activity} label="傳輸任務" count={activeCount} onClick={() => setPage("transfers")} />
          <NavButton active={page === "library"} icon={Library} label="模型庫" onClick={() => setPage("library")} />
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
          {page === "new" && <NewDownload onCreated={() => { void refreshTasks(); setPage("transfers") }} />}
          {page === "transfers" && <Transfers tasks={tasks} isAdmin={isAdmin} refresh={refreshTasks} />}
          {page === "library" && <LibraryPage tasks={tasks} />}
          {page === "settings" && <SettingsPage isAdmin={isAdmin} />}
        </div>
      </main>

      <nav className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-4 rounded-2xl border border-white/10 bg-[#0c1219]/95 p-1.5 shadow-2xl backdrop-blur-xl lg:hidden">
        <MobileNav active={page === "new"} icon={Plus} label="新增" onClick={() => setPage("new")} />
        <MobileNav active={page === "transfers"} icon={Activity} label="任務" onClick={() => setPage("transfers")} />
        <MobileNav active={page === "library"} icon={Library} label="模型" onClick={() => setPage("library")} />
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

function NewDownload({ onCreated }: { onCreated: () => void }) {
  const [source, setSource] = useState("https://huggingface.co/Comfy-Org/z_image_turbo/tree/main")
  const [token, setToken] = useState("")
  const [repo, setRepo] = useState<RepoResolution | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const selectedBytes = useMemo(() => repo?.files.filter((file) => selected.has(file.path)).reduce((sum, file) => sum + file.size, 0) ?? 0, [repo, selected])

  const resolve = async () => {
    setBusy(true); setError("")
    try {
      const result = await api.resolveRepo(source, token)
      setRepo(result)
      setSelected(new Set(result.files.map((file) => file.path)))
    } catch (reason) { setError(reason instanceof Error ? reason.message : "讀取 repo 失敗") }
    finally { setBusy(false) }
  }
  const create = async () => {
    if (!repo || !selected.size) return
    setBusy(true); setError("")
    try {
      await api.createTask(source, [...selected], token)
      setToken("")
      onCreated()
    } catch (reason) { setError(reason instanceof Error ? reason.message : "建立任務失敗") }
    finally { setBusy(false) }
  }

  return <>
    <PageHeading eyebrow="New transfer" title="從 Hugging Face 取得模型" description="貼上 Model ID 或網址，選擇需要的檔案。Token 只在此任務的記憶體中使用，不會保存到伺服器。" />
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Card>
        <CardHeader><h2 className="font-semibold text-white">Repo 來源</h2><p className="text-xs text-slate-500">支援 owner/repo、模型首頁與 /tree/&lt;revision&gt;</p></CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row"><Input value={source} onChange={(event) => setSource(event.target.value)} placeholder="Comfy-Org/z_image_turbo" onKeyDown={(event) => event.key === "Enter" && void resolve()} /><Button className="sm:w-28" onClick={() => void resolve()} disabled={busy || !source.trim()}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}解析</Button></div>
          <div className="relative"><KeyRound className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-600" /><Input className="pl-10" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="HF Token（選填；不儲存）" /></div>
          {error && <div className="flex items-start gap-2 rounded-lg border border-rose-400/15 bg-rose-400/[.07] p-3 text-sm text-rose-300"><XCircle className="mt-0.5 size-4 shrink-0" />{error}</div>}
          {repo && <div className="space-y-4 pt-2">
            <div className="flex flex-wrap items-center gap-2"><Badge className="border-cyan-400/20 bg-cyan-400/10 text-cyan-300">{repo.repo_id}</Badge><Badge>{repo.requested_revision}</Badge><span className="font-mono text-[10px] text-slate-600">{repo.commit_hash.slice(0, 12)}</span></div>
            <FileTree files={repo.files} selected={selected} onChange={setSelected} />
            <div className="flex flex-col items-start justify-between gap-3 rounded-xl border border-white/[.07] bg-white/[.025] p-4 sm:flex-row sm:items-center"><div><div className="text-sm font-medium text-slate-200">已選 {selected.size} / {repo.files.length} 個檔案</div><div className="mt-1 text-xs text-slate-500">合計 {formatBytes(selectedBytes)} · 儲存於服務端共用模型庫</div></div><Button onClick={() => void create()} disabled={busy || !selected.size}><Play className="size-4 fill-current" />建立下載任務</Button></div>
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

function MetricCard({ icon: Icon, label, value, note }: { icon: typeof Archive; label: string; value: string; note: string }) {
  return <Card><CardContent className="p-4"><div className="mb-4 flex items-center justify-between"><span className="text-[10px] font-bold uppercase tracking-[.18em] text-slate-600">{label}</span><Icon className="size-4 text-cyan-400/60" /></div><div className="text-xl font-semibold text-slate-100">{value}</div><div className="mt-1 text-xs text-slate-600">{note}</div></CardContent></Card>
}

function Transfers({ tasks, isAdmin, refresh }: { tasks: DownloadTask[]; isAdmin: boolean; refresh: () => Promise<void> }) {
  const active = tasks.filter((task) => task.status !== "completed" && task.status !== "cancelled")
  return <><PageHeading eyebrow="Transfers" title="下載任務" description="即時查看服務端取得進度。暫停會停止排程並終止正在執行的檔案 worker。" action={<div className="flex items-center gap-2 text-xs text-slate-500"><CircleGauge className="size-4 text-cyan-400" />{active.length} active</div>} />
    <div className="space-y-4">{tasks.length ? tasks.map((task) => <TaskCard key={task.id} task={task} isAdmin={isAdmin} refresh={refresh} />) : <EmptyState icon={Activity} title="尚無下載任務" text="從「新增下載」解析第一個 Hugging Face repo。" />}</div>
  </>
}

function TaskCard({ task, isAdmin, refresh }: { task: DownloadTask; isAdmin: boolean; refresh: () => Promise<void> }) {
  const [expanded, setExpanded] = useState(false)
  const [token, setToken] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const meta = statusMeta[task.status] ?? { label: task.status, className: "" }
  const progress = percent(task.downloaded_bytes, task.total_bytes)
  const command = async (action: "pause" | "resume" | "retry" | "cancel") => {
    setBusy(true); setError("")
    try { await api.command(task.id, action, token); setToken(""); await refresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失敗") }
    finally { setBusy(false) }
  }
  const remove = async () => {
    if (!window.confirm("要移除此任務及未被其他任務引用的下載檔案嗎？")) return
    setBusy(true); setError("")
    try { await api.deleteTask(task.id, true); await refresh() }
    catch (reason) { setError(reason instanceof Error ? reason.message : "刪除失敗") }
    finally { setBusy(false) }
  }
  return <Card className="overflow-hidden">
    <div className="p-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-center">
        <div className="grid size-11 shrink-0 place-items-center rounded-xl border border-white/[.07] bg-[#0a1016]"><Box className="size-5 text-cyan-400/75" /></div>
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="truncate font-semibold text-slate-100">{task.repo_id}</h3><Badge className={meta.className}>{meta.label}</Badge></div><div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-600"><span>{task.requested_revision}</span><span className="font-mono">{task.commit_hash.slice(0, 10)}</span><span>{task.files.length} files</span></div></div>
        {isAdmin && <div className="flex items-center gap-1.5">
          {["queued", "downloading"].includes(task.status) && <Button variant="secondary" size="sm" disabled={busy} onClick={() => void command("pause")}><Pause className="size-3.5" />暫停</Button>}
          {["paused", "auth_required"].includes(task.status) && <Button size="sm" disabled={busy} onClick={() => void command("resume")}><Play className="size-3.5" />繼續</Button>}
          {["failed", "partial"].includes(task.status) && <Button size="sm" disabled={busy} onClick={() => void command("retry")}><RotateCcw className="size-3.5" />重試</Button>}
          {!task.status.match(/completed|cancelled/) && <Button variant="ghost" size="icon" disabled={busy} onClick={() => void command("cancel")} aria-label="取消"><Square className="size-3.5" /></Button>}
          {["completed", "cancelled", "failed", "partial", "paused"].includes(task.status) && <Button variant="destructive" size="icon" disabled={busy} onClick={() => void remove()} aria-label="刪除"><Trash2 className="size-3.5" /></Button>}
        </div>}
      </div>
      {task.status === "auth_required" && isAdmin && <div className="mt-4 flex gap-2"><Input type="password" autoComplete="off" className="h-9" placeholder="重新提供 HF Token" value={token} onChange={(event) => setToken(event.target.value)} /><Button size="sm" onClick={() => void command("resume")} disabled={!token || busy}>驗證並繼續</Button></div>}
      <div className="mt-5"><div className="mb-2 flex flex-wrap justify-between gap-2 text-xs"><span className="text-slate-500">{formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}</span><span className="flex gap-3 font-mono text-slate-500"><span>{task.status === "downloading" ? `${formatBytes(task.speed_bps)}/s` : "— B/s"}</span><span>ETA {task.status === "downloading" ? formatEta(task.eta_seconds) : "—"}</span><span className="text-slate-300">{progress}%</span></span></div><Progress value={progress} /></div>
      {(error || task.error) && <div className="mt-3 text-xs text-rose-300">{error || task.error}</div>}
      <button onClick={() => setExpanded(!expanded)} className="mt-4 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300"><ChevronDown className={cn("size-3.5 transition-transform", expanded && "rotate-180")} />{expanded ? "收合檔案" : "查看檔案"}</button>
    </div>
    {expanded && <div className="max-h-72 overflow-auto border-t border-white/[.06] bg-[#0a1016]/70">{task.files.map((file) => <div key={file.id} className="flex items-center gap-3 border-b border-white/[.035] px-5 py-2.5 text-xs"><FileStatus status={file.status} /><span className="min-w-0 flex-1 truncate text-slate-400">{file.path}</span><span className="font-mono text-slate-600">{formatBytes(file.size)}</span>{file.status === "completed" && <a className="text-cyan-400 hover:text-cyan-300" href={fileDownloadUrl(task.id, file.path)}><FileDown className="size-4" /></a>}</div>)}</div>}
  </Card>
}

function FileStatus({ status }: { status: string }) {
  if (status === "completed") return <CheckCircle2 className="size-4 shrink-0 text-emerald-400" />
  if (status === "downloading") return <LoaderCircle className="size-4 shrink-0 animate-spin text-cyan-400" />
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-rose-400" />
  if (status === "paused") return <Pause className="size-4 shrink-0 text-amber-400" />
  return <span className="size-2 shrink-0 rounded-full bg-slate-700" />
}

function LibraryPage({ tasks }: { tasks: DownloadTask[] }) {
  const completed = tasks.filter((task) => task.files.some((file) => file.status === "completed"))
  return <><PageHeading eyebrow="Shared library" title="LAN 模型庫" description="所有完成檔案都可以逐檔下載；瀏覽器會利用 HTTP Range 處理大型檔案續傳。" />
    <div className="grid gap-4 md:grid-cols-2">{completed.length ? completed.map((task) => <Card key={task.id}><CardHeader><div className="flex items-start justify-between gap-3"><div><h3 className="font-semibold text-white">{task.repo_id}</h3><div className="mt-1 font-mono text-[10px] text-slate-600">{task.commit_hash.slice(0, 12)}</div></div><Badge className="border-emerald-400/20 bg-emerald-400/10 text-emerald-300">{task.files.filter((file) => file.status === "completed").length} files</Badge></div></CardHeader><CardContent><div className="max-h-56 space-y-1 overflow-auto">{task.files.filter((file) => file.status === "completed").map((file) => <a key={file.id} href={fileDownloadUrl(task.id, file.path)} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-slate-400 hover:bg-white/[.04] hover:text-cyan-300"><FileDown className="size-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate">{file.path}</span><span className="font-mono text-slate-600">{formatBytes(file.size)}</span></a>)}</div></CardContent></Card>) : <div className="md:col-span-2"><EmptyState icon={Library} title="模型庫還是空的" text="任務完成的檔案會自動出現在這裡。" /></div>}</div>
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
      <SettingField label="容量上限（GiB）" note="0 代表不限容量"><Input type="number" min={0} value={Math.round(settings.max_storage_bytes / 1024 ** 3)} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, max_storage_bytes: Number(e.target.value) * 1024 ** 3 })} /></SettingField>
      <SettingField label="磁碟安全保留（GiB）" note="低於此空間時拒絕新任務"><Input type="number" min={0} value={Math.round(settings.min_free_bytes / 1024 ** 3)} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, min_free_bytes: Number(e.target.value) * 1024 ** 3 })} /></SettingField>
      <SettingField label="保留天數" note="0 代表不自動過期；自動清理將於後續版本啟用"><Input type="number" min={0} value={settings.retention_days} disabled={!isAdmin} onChange={(e) => setSettings({ ...settings, retention_days: Number(e.target.value) })} /></SettingField>
      <SettingField label="允許刪除實體檔案" note="關閉後只能保留共用模型內容"><button type="button" disabled={!isAdmin} onClick={() => setSettings({ ...settings, allow_delete_files: !settings.allow_delete_files })} className={cn("relative h-7 w-12 rounded-full border transition", settings.allow_delete_files ? "border-cyan-400/40 bg-cyan-400/25" : "border-white/10 bg-white/[.04]")}><span className={cn("absolute top-1 size-4 rounded-full bg-white transition-all", settings.allow_delete_files ? "left-7" : "left-1")} /></button></SettingField>
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
