import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router'

/** Scrolls to the top when the pathname changes. Hash links are left to the page that owns the target, and the first render is left alone. */
export function ScrollToTop() {
  const { pathname, hash } = useLocation()
  const previous = useRef<string | null>(null)

  useEffect(() => {
    const changed = previous.current !== null && previous.current !== pathname
    previous.current = pathname
    if (!changed) return
    if (!hash) window.scrollTo(0, 0)
  }, [pathname, hash])

  return null
}
