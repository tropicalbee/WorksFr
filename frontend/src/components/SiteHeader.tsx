import { useEffect, useId, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Menu, X } from 'lucide-react'
import { Link } from 'react-router'
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover'

export type SiteNavLink = { label: string; to: string; icon?: LucideIcon; current?: boolean }
export type SiteHeaderProps = { nav?: SiteNavLink[]; navLabel?: string }

export function SiteHeader({ nav = [], navLabel }: SiteHeaderProps) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const desktopNav = useRef<HTMLElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1024px)')
    const closeOnDesktop = () => { if (desktop.matches) setOpen(false) }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])

  return (
    <header className="masthead">
      <span className="wordmark">WorksFr<span className="wordmark-period">.</span></span>
      {nav.length ? (
        <>
          <div className="preview-tools desktop-navigation">
            {navLabel && <span className="metadata preview-label">{navLabel}</span>}
            <nav className="preview-controls" aria-label={navLabel ?? 'Site navigation'} ref={desktopNav}>
              {nav.map(({ label, to, icon: Icon, current }) => (
                <Link key={to} to={to} aria-current={current ? 'page' : undefined}>
                  {Icon && <Icon aria-hidden="true" />}
                  {label}
                </Link>
              ))}
            </nav>
          </div>
          <Popover modal open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="mobile-menu-trigger"
                aria-label="Menu"
                aria-expanded={open}
                aria-controls={panelId}
              >
                {open ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
              </button>
            </PopoverTrigger>
            <PopoverPortal>
              <div className="mobile-menu-backdrop" data-state={open ? 'open' : 'closed'} aria-hidden="true" />
            </PopoverPortal>
            <PopoverContent
              ref={panel}
              id={panelId}
              className="mobile-menu-panel"
              collisionPadding={10}
              aria-label="Menu"
              onOpenAutoFocus={event => {
                event.preventDefault()
                const current = panel.current?.querySelector<HTMLAnchorElement>('[aria-current="page"]')
                const target = current ?? panel.current?.querySelector<HTMLAnchorElement>('a')
                target?.focus()
              }}
              onCloseAutoFocus={event => {
                if (window.matchMedia('(min-width: 1024px)').matches) {
                  event.preventDefault()
                  const links = desktopNav.current
                  const current = links?.querySelector<HTMLAnchorElement>('[aria-current="page"]')
                  const target = current ?? links?.querySelector<HTMLAnchorElement>('a')
                  target?.focus()
                }
              }}
            >
              {navLabel && <p className="mobile-menu-label">{navLabel}</p>}
              <nav aria-label={navLabel ?? 'Site navigation'}>
                {nav.map(({ label, to, icon: Icon, current }) => (
                  <Link
                    key={to}
                    to={to}
                    aria-current={current ? 'page' : undefined}
                    onClick={() => setOpen(false)}
                  >
                    {Icon && <Icon aria-hidden="true" />}
                    {label}
                  </Link>
                ))}
              </nav>
            </PopoverContent>
          </Popover>
        </>
      ) : <span className="metadata">Tutorial check</span>}
    </header>
  )
}
