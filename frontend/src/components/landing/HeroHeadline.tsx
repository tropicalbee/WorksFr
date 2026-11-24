import { useEffect, useRef, useState } from 'react'

const TYPE_MS = 55
const ERASE_MS = 25
const HOLD_MS = 3000
const RETYPE_PAUSE_MS = 600

const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

type HeroHeadlineProps = { lead: string; tails: string[] }

/**
 * Types `lead` plus the first tail, holds it, erases only the tail, then types the next one, forever.
 * Every full sentence is stacked invisibly in one grid cell, so the box always takes the tallest wrap
 * and the height never changes. Screen readers get the first full sentence once. The loop pauses
 * while the tab is hidden or the headline is off screen, and resumes where it stopped.
 */
export function HeroHeadline({ lead, tails }: HeroHeadlineProps) {
  const sentences = tails.map(tail => `${lead} ${tail}`)
  const [reduced] = useState(prefersReducedMotion)
  const [count, setCount] = useState(reduced ? sentences[0].length : 0)
  const [tailIndex, setTailIndex] = useState(0)
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    const el = heading.current
    if (reduced || !el) return
    const full = tails.map(tail => `${lead} ${tail}`)
    let index = 0
    let length = 0
    let phase: 'type' | 'erase' = 'type'
    let nextDelay = TYPE_MS
    let timer: number | undefined
    let onScreen = true

    const playing = () => onScreen && document.visibilityState === 'visible'
    const schedule = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(step, nextDelay)
    }
    function step() {
      if (phase === 'type') {
        length += 1
        nextDelay = TYPE_MS
        if (length >= full[index].length) {
          phase = 'erase'
          nextDelay = HOLD_MS
        }
      } else {
        length -= 1
        nextDelay = ERASE_MS
        if (length <= lead.length) {
          phase = 'type'
          nextDelay = RETYPE_PAUSE_MS
          index = (index + 1) % full.length
          setTailIndex(index)
        }
      }
      setCount(length)
      if (playing()) schedule()
    }
    const sync = () => {
      if (playing()) schedule()
      else window.clearTimeout(timer)
    }

    const observer = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting
      sync()
    })
    observer.observe(el)
    document.addEventListener('visibilitychange', sync)
    schedule()
    return () => {
      observer.disconnect()
      document.removeEventListener('visibilitychange', sync)
      window.clearTimeout(timer)
    }
  }, [reduced, lead, tails])

  const current = sentences[tailIndex]

  return (
    <h1 ref={heading}>
      <span className="sr-only">{sentences[0]}</span>
      <span className="lp-hl" aria-hidden="true">
        {sentences.map(sentence => <span key={sentence} className="lp-hl-size">{sentence}</span>)}
        <span className="lp-hl-typed">
          {current.slice(0, count)}
          {!reduced && <span className="lp-caret" />}
          <span className="invisible">{current.slice(count)}</span>
        </span>
      </span>
    </h1>
  )
}
