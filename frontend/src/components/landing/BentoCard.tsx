import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { useInViewOnce } from './useInViewOnce'

const REPLAY_AFTER_MS = 4200

type BentoCardProps = {
  className?: string
  title: string
  text: string
  /** One effect per card at most: either a texture inside the stage or a border sweep. */
  texture?: ReactNode
  border?: ReactNode
  children: ReactNode
}

export function BentoCard({ className, title, text, texture, border, children }: BentoCardProps) {
  const [ref, inView] = useInViewOnce<HTMLElement>()
  const [run, setRun] = useState(0)
  const startedAt = useRef<number | null>(null)

  useEffect(() => {
    if (inView) startedAt.current = Date.now()
  }, [inView])

  function replay() {
    if (!inView) return
    const now = Date.now()
    if (startedAt.current !== null && now - startedAt.current < REPLAY_AFTER_MS) return
    startedAt.current = now
    setRun(value => value + 1)
  }

  return (
    <article ref={ref} className={cn('lp-card', className)} data-in={inView} onMouseEnter={replay}>
      {border}
      <div className="lp-stage">
        {texture}
        <div key={run} className="lp-vis">{children}</div>
      </div>
      <div className="lp-copy">
        <h3>{title}</h3>
        <p>{text}</p>
      </div>
    </article>
  )
}
