import { AlertTriangle, KeyRound, MessageSquareText, X } from "lucide-react"
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from "react"
import { createPortal } from "react-dom"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

type DialogTone = "default" | "danger"

type ConfirmOptions = {
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: DialogTone
}

type PromptOptions = ConfirmOptions & {
  label: string
  initialValue?: string
  placeholder?: string
  type?: "text" | "password"
  required?: boolean
  maxLength?: number
}

type DialogRequest =
  | { id: number; kind: "confirm"; options: ConfirmOptions; resolve: (value: boolean) => void }
  | { id: number; kind: "prompt"; options: PromptOptions; value: string; resolve: (value: string | null) => void }

type AppDialogContextValue = {
  confirm: (options: ConfirmOptions) => Promise<boolean>
  prompt: (options: PromptOptions) => Promise<string | null>
}

const AppDialogContext = createContext<AppDialogContextValue | null>(null)

export function useAppDialog() {
  const value = useContext(AppDialogContext)
  if (!value) throw new Error("useAppDialog must be used inside AppDialogProvider")
  return value
}

export function AppDialogProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<DialogRequest | null>(null)
  const requestRef = useRef<DialogRequest | null>(null)
  const nextRequestId = useRef(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()
  const descriptionId = useId()

  requestRef.current = request

  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => {
    setRequest({ id: ++nextRequestId.current, kind: "confirm", options, resolve })
  }), [])

  const prompt = useCallback((options: PromptOptions) => new Promise<string | null>((resolve) => {
    setRequest({ id: ++nextRequestId.current, kind: "prompt", options, value: options.initialValue ?? "", resolve })
  }), [])

  const cancel = useCallback(() => {
    const current = requestRef.current
    if (!current) return
    if (current.kind === "confirm") current.resolve(false)
    else current.resolve(null)
    setRequest(null)
  }, [])

  const accept = useCallback(() => {
    const current = requestRef.current
    if (!current) return
    if (current.kind === "prompt") {
      if (current.options.required !== false && !current.value.trim()) return
      current.resolve(current.value)
    } else {
      current.resolve(true)
    }
    setRequest(null)
  }, [])

  useEffect(() => {
    if (!request) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    const focusTimer = window.setTimeout(() => {
      if (request.kind === "prompt") {
        inputRef.current?.focus()
        inputRef.current?.select()
      } else {
        cancelRef.current?.focus()
      }
    }, 0)
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancel()
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      window.clearTimeout(focusTimer)
      document.removeEventListener("keydown", onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [cancel, request?.id])

  const options = request?.options
  const tone = options?.tone ?? "default"
  const isPrompt = request?.kind === "prompt"
  const promptOptions = isPrompt ? request.options : null
  const submitDisabled = Boolean(request?.kind === "prompt" && request.options.required !== false && !request.value.trim())

  return <AppDialogContext.Provider value={{ confirm, prompt }}>
    {children}
    {request && createPortal(
      <div
        className="hfdm-dialog-backdrop fixed inset-0 z-[100] grid place-items-center overflow-y-auto bg-slate-950/80 px-4 py-8 backdrop-blur-sm"
        onMouseDown={(event) => { if (event.target === event.currentTarget) cancel() }}
      >
        <section
          className="hfdm-dialog-panel relative w-full max-w-md overflow-hidden rounded-2xl border border-white/10 bg-[#101821] shadow-[0_28px_90px_rgba(0,0,0,.62),0_0_0_1px_rgba(34,211,238,.025)]"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={options?.description ? descriptionId : undefined}
        >
          <div className={cn("absolute inset-x-0 top-0 h-px", tone === "danger" ? "bg-gradient-to-r from-transparent via-rose-400/70 to-transparent" : "bg-gradient-to-r from-transparent via-cyan-300/70 to-transparent")} />
          <button type="button" onClick={cancel} className="absolute right-4 top-4 grid size-8 place-items-center rounded-lg text-slate-500 transition hover:bg-white/[.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60" aria-label="關閉對話框">
            <X className="size-4" />
          </button>

          <form onSubmit={(event) => { event.preventDefault(); accept() }}>
            <div className="px-6 pb-5 pt-6 sm:px-7 sm:pt-7">
              <div className={cn("mb-5 grid size-11 place-items-center rounded-xl border", tone === "danger" ? "border-rose-400/20 bg-rose-400/10 text-rose-300" : isPrompt && promptOptions?.type === "password" ? "border-violet-400/20 bg-violet-400/10 text-violet-300" : "border-cyan-400/20 bg-cyan-400/10 text-cyan-300")}>
                {tone === "danger" ? <AlertTriangle className="size-5" /> : isPrompt && promptOptions?.type === "password" ? <KeyRound className="size-5" /> : <MessageSquareText className="size-5" />}
              </div>
              <h2 id={titleId} className="pr-9 text-lg font-semibold tracking-tight text-white">{options?.title}</h2>
              {options?.description && <div id={descriptionId} className="mt-2 whitespace-pre-line text-sm leading-6 text-slate-400">{options.description}</div>}

              {promptOptions && <label className="mt-5 block">
                <span className="mb-2 block text-[11px] font-semibold uppercase tracking-[.14em] text-slate-500">{promptOptions.label}</span>
                <Input
                  ref={inputRef}
                  type={promptOptions.type ?? "text"}
                  value={request.kind === "prompt" ? request.value : ""}
                  onChange={(event) => setRequest((current) => current?.kind === "prompt" ? { ...current, value: event.target.value } : current)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                      event.preventDefault()
                      accept()
                    }
                  }}
                  placeholder={promptOptions.placeholder}
                  maxLength={promptOptions.maxLength}
                  autoComplete={promptOptions.type === "password" ? "off" : undefined}
                  spellCheck={promptOptions.type === "password" ? false : undefined}
                  className="h-12 rounded-xl bg-[#091017]"
                />
              </label>}
            </div>

            <div className="flex flex-col-reverse gap-2 border-t border-white/[.06] bg-black/10 px-6 py-4 sm:flex-row sm:justify-end sm:px-7">
              <Button ref={cancelRef} type="button" variant="ghost" onClick={cancel} className="sm:min-w-24">{options?.cancelLabel ?? "取消"}</Button>
              <Button type="submit" variant={tone === "danger" ? "destructive" : "default"} disabled={submitDisabled} className="sm:min-w-24">{options?.confirmLabel ?? (isPrompt ? "儲存" : "確認")}</Button>
            </div>
          </form>
        </section>
      </div>,
      document.body,
    )}
  </AppDialogContext.Provider>
}
