import * as ProgressPrimitive from "@radix-ui/react-progress"

import { cn } from "@/lib/utils"

export function Progress({ value = 0, className }: { value?: number; className?: string }) {
  return (
    <ProgressPrimitive.Root className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-white/[.07]", className)} value={value}>
      <ProgressPrimitive.Indicator
        className="h-full bg-gradient-to-r from-cyan-500 to-cyan-300 transition-transform duration-500"
        style={{ transform: `translateX(-${100 - value}%)` }}
      />
    </ProgressPrimitive.Root>
  )
}
