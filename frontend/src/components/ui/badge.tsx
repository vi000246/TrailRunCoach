import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        default:  'bg-[#7c3aed]/20 text-[#a78bfa]',
        cycling:  'bg-[#7c3aed]/20 text-[#a78bfa]',
        running:  'bg-[#06b6d4]/20 text-[#06b6d4]',
        swimming: 'bg-[#3b82f6]/20 text-[#3b82f6]',
        strength: 'bg-[#f59e0b]/20 text-[#f59e0b]',
        other:    'bg-[#1c2333] text-[#7d8fa6]',
        green:    'bg-[#22c55e]/20 text-[#22c55e]',
        red:      'bg-[#ef4444]/20 text-[#ef4444]',
      },
    },
    defaultVariants: { variant: 'default' },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
