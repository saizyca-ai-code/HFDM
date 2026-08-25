import { forwardRef, type InputHTMLAttributes } from "react"

import { cn } from "@/lib/utils"

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...props }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-11 w-full rounded-lg border border-white/10 bg-[#0b1118] px-3.5 text-sm text-slate-100 outline-none transition placeholder:text-slate-600 focus:border-cyan-400/60 focus:ring-2 focus:ring-cyan-400/10 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  )
})
