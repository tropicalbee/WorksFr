// Adapted from Magic UI "Animated Shiny Text" (magicui.design). Two slow passes, then it rests.
// Keyframes live in components/landing/landing.css.
import type { ReactNode } from 'react'

export function ShinyText({ children }: { children: ReactNode }) {
  return <span className="ui-shiny-text">{children}</span>
}
