import * as React from 'react'
import { cn } from '../../lib/utils'

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      'h-8 w-full rounded-md border border-[#1c2333] bg-[#141922] px-3 py-1.5 text-sm text-[#e8edf5] placeholder:text-[#3e4e63] focus:outline-none focus:ring-1 focus:ring-[#7c3aed] disabled:opacity-50',
      className
    )}
    {...props}
  />
))
Input.displayName = 'Input'

export { Input }
