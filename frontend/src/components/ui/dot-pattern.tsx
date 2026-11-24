// Adapted from Magic UI "Dot Pattern" (magicui.design), SVG pattern only, no motion.
import { useId } from 'react'
import { cn } from '@/lib/utils'

export function DotPattern({ className, size = 16, radius = 1 }: { className?: string; size?: number; radius?: number }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  return (
    <svg aria-hidden="true" className={cn('pointer-events-none absolute inset-0 !size-full text-white/10', className)}>
      <defs>
        <pattern id={id} width={size} height={size} patternUnits="userSpaceOnUse">
          <circle cx={size / 2} cy={size / 2} r={radius} fill="currentColor" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  )
}
