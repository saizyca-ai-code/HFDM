import { ChevronDown, ChevronRight, File, Folder, Search } from "lucide-react"
import { useMemo, useState } from "react"

import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { cn, formatBytes } from "@/lib/utils"
import type { RepoFile } from "@/lib/api"

type TreeNode = {
  name: string
  path: string
  children: TreeNode[]
  file?: RepoFile
}

function buildTree(files: RepoFile[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", children: [] }
  for (const file of files) {
    let cursor = root
    const parts = file.path.split("/")
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/")
      let child = cursor.children.find((item) => item.name === part)
      if (!child) {
        child = { name: part, path, children: [] }
        cursor.children.push(child)
      }
      cursor = child
      if (index === parts.length - 1) cursor.file = file
    })
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (Boolean(a.file) !== Boolean(b.file)) return a.file ? 1 : -1
      return a.name.localeCompare(b.name)
    })
    nodes.forEach((node) => sort(node.children))
  }
  sort(root.children)
  return root.children
}

function leafPaths(node: TreeNode): string[] {
  if (node.file) return [node.file.path]
  return node.children.flatMap(leafPaths)
}

function NodeRow({
  node,
  depth,
  selected,
  expanded,
  toggleExpanded,
  togglePaths,
}: {
  node: TreeNode
  depth: number
  selected: Set<string>
  expanded: Set<string>
  toggleExpanded: (path: string) => void
  togglePaths: (paths: string[], checked: boolean) => void
}) {
  const paths = leafPaths(node)
  const selectedCount = paths.filter((path) => selected.has(path)).length
  const checked: boolean | "indeterminate" = selectedCount === paths.length ? true : selectedCount ? "indeterminate" : false
  const isFolder = !node.file
  const isOpen = expanded.has(node.path)

  return (
    <>
      <div className="group flex h-9 items-center gap-2 border-b border-white/[.035] pr-3 text-sm hover:bg-white/[.025]" style={{ paddingLeft: 12 + depth * 20 }}>
        {isFolder ? (
          <button className="grid size-5 place-items-center text-slate-500 hover:text-slate-200" onClick={() => toggleExpanded(node.path)} aria-label={isOpen ? "收合資料夾" : "展開資料夾"}>
            {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          </button>
        ) : <span className="size-5" />}
        <Checkbox checked={checked} onCheckedChange={(value) => togglePaths(paths, value)} />
        {isFolder ? <Folder className="size-4 text-cyan-400/70" /> : <File className="size-4 text-slate-500" />}
        <span className={cn("min-w-0 flex-1 truncate", isFolder ? "font-medium text-slate-300" : "text-slate-400")}>{node.name}</span>
        {node.file ? <span className="font-mono text-[11px] text-slate-600">{formatBytes(node.file.size)}</span> : <span className="text-[11px] text-slate-600">{paths.length} files</span>}
      </div>
      {isFolder && isOpen && node.children.map((child) => (
        <NodeRow key={child.path} node={child} depth={depth + 1} selected={selected} expanded={expanded} toggleExpanded={toggleExpanded} togglePaths={togglePaths} />
      ))}
    </>
  )
}

export function FileTree({ files, selected, onChange }: { files: RepoFile[]; selected: Set<string>; onChange: (next: Set<string>) => void }) {
  const [query, setQuery] = useState("")
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const tree = useMemo(() => buildTree(files), [files])
  const filtered = query.trim() ? files.filter((file) => file.path.toLowerCase().includes(query.trim().toLowerCase())) : null

  const togglePaths = (paths: string[], checked: boolean) => {
    const next = new Set(selected)
    paths.forEach((path) => checked ? next.add(path) : next.delete(path))
    onChange(next)
  }
  const toggleExpanded = (path: string) => {
    const next = new Set(expanded)
    next.has(path) ? next.delete(path) : next.add(path)
    setExpanded(next)
  }

  return (
    <div className="overflow-hidden rounded-xl border border-white/[.07] bg-[#0b1118]">
      <div className="flex items-center gap-3 border-b border-white/[.07] p-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-600" />
          <Input className="h-9 pl-9" placeholder="搜尋檔名或路徑" value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
        <button className="text-xs font-medium text-cyan-400 hover:text-cyan-300" onClick={() => onChange(new Set(files.map((file) => file.path)))}>全選</button>
        <button className="text-xs font-medium text-slate-500 hover:text-slate-300" onClick={() => onChange(new Set())}>清除</button>
      </div>
      <div className="max-h-[430px] overflow-auto">
        {filtered ? filtered.map((file) => (
          <div key={file.path} className="flex h-9 items-center gap-2 border-b border-white/[.035] px-3 text-sm">
            <Checkbox checked={selected.has(file.path)} onCheckedChange={(checked) => togglePaths([file.path], checked)} />
            <File className="size-4 text-slate-500" />
            <span className="min-w-0 flex-1 truncate text-slate-400">{file.path}</span>
            <span className="font-mono text-[11px] text-slate-600">{formatBytes(file.size)}</span>
          </div>
        )) : tree.map((node) => (
          <NodeRow key={node.path} node={node} depth={0} selected={selected} expanded={expanded} toggleExpanded={toggleExpanded} togglePaths={togglePaths} />
        ))}
      </div>
    </div>
  )
}
