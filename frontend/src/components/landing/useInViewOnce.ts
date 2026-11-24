import { useEffect, useRef, useState } from 'react'

/** True once the element has scrolled into view. It never goes back to false. */
export function useInViewOnce<T extends Element>(threshold = 0.3) {
  const ref = useRef<T>(null)
  const [seen, setSeen] = useState(() => typeof IntersectionObserver === 'undefined')

  useEffect(() => {
    const el = ref.current
    if (seen || !el) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setSeen(true)
        observer.disconnect()
      }
    }, { threshold })
    observer.observe(el)
    return () => observer.disconnect()
  }, [seen, threshold])

  return [ref, seen] as const
}
