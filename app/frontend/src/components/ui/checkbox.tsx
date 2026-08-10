import * as CheckboxPrimitive from "@radix-ui/react-checkbox"
import { Check, Minus } from "lucide-react"

import { cn } from "@/lib/utils"

export function Checkbox({
  checked,
  onCheckedChange,
  className,
}: {
  checked: boolean | "indeterminate"
  onCheckedChange: (checked: boolean) => void
  className?: string
}) {
  return (
    <CheckboxPrimitive.Root
      checked={checked}
      onCheckedChange={(value) => onCheckedChange(value === true)}
      className={cn(
        "grid size-4 shrink-0 place-items-center rounded border border-slate-600 bg-[#0a1016] text-slate-950 data-[state=checked]:border-cyan-400 data-[state=checked]:bg-cyan-400 data-[state=indeterminate]:border-cyan-400 data-[state=indeterminate]:bg-cyan-400",
        className,
      )}
    >
      <CheckboxPrimitive.Indicator>
        {checked === "indeterminate" ? <Minus className="size-3" /> : <Check className="size-3" />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}
