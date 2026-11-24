// Adapted from Magic UI "Grid Pattern" (magicui.design), SVG pattern only, no motion.
import { useId } from 'react'
import { cn } from '@/lib/utils'

export function GridPattern({ className, size = 24 }: { className?: string; size?: number }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  return (
    <svg aria-hidden="true" className={cn('pointer-events-none absolute inset-0 !size-full text-white/[0.04]', className)}>
      <defs>
        <pattern id={id} width={size} height={size} patternUnits="userSpaceOnUse">
          <path d={`M ${size} 0 L 0 0 0 ${size}`} fill="none" stroke="currentColor" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  )
}
