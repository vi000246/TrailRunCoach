import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#7c3aed] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default:   'bg-[#7c3aed] text-white hover:bg-[#6d28d9]',
        outline:   'border border-[#1c2333] bg-transparent text-[#e8edf5] hover:bg-[#141922]',
        ghost:     'text-[#7d8fa6] hover:bg-[#141922] hover:text-[#e8edf5]',
        secondary: 'bg-[#141922] text-[#e8edf5] hover:bg-[#1c2333]',
        destructive: 'bg-[#ef4444] text-white hover:bg-[#dc2626]',
        link:      'text-[#a78bfa] underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-8 px-3 py-1.5',
        sm:      'h-7 px-2.5 text-xs',
        lg:      'h-10 px-4',
        icon:    'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    )
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
