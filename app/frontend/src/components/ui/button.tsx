import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { forwardRef, type ButtonHTMLAttributes } from "react"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60 disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "bg-cyan-400 text-slate-950 shadow-[0_0_20px_rgba(34,211,238,.18)] hover:bg-cyan-300",
        secondary: "border border-white/10 bg-white/[.06] text-slate-100 hover:bg-white/10",
        ghost: "text-slate-400 hover:bg-white/[.06] hover:text-slate-100",
        destructive: "border border-rose-400/20 bg-rose-400/10 text-rose-300 hover:bg-rose-400/20",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
)

type Props = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }

export const Button = forwardRef<HTMLButtonElement, Props>(function Button({ className, variant, size, asChild, ...props }, ref) {
  const Comp = asChild ? Slot : "button"
  return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
})
