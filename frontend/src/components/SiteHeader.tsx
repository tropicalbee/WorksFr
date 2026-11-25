import { useEffect, useId, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import type { LucideIcon } from 'lucide-react'
import { Menu, X } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router'
import { isShowcase } from '@/api/checkSource'
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover'
import { focusCheckInput } from './checkInput'
import './site-header.css'

const REPO_URL = 'https://github.com/tropicalbee/WorksFr'

function GithubMark() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  )
}

export type SiteNavLink = { label: string; to: string; icon?: LucideIcon; current?: boolean }
export type SiteHeaderProps = { nav?: SiteNavLink[]; navLabel?: string; width?: 'landing' | 'report' }

export function SiteHeader({ nav: navProp, navLabel, width = 'landing' }: SiteHeaderProps) {
  const { pathname, hash } = useLocation()
  const navigate = useNavigate()
  const onHome = pathname === '/'
  const nav: SiteNavLink[] = navProp ?? [
    { label: 'How it works', to: '/#how', current: onHome && hash === '#how' },
    { label: 'Recent checks', to: '/#recent', current: onHome && hash === '#recent' },
  ]
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(() => window.scrollY > 8)
  const panelId = useId()
  const desktopNav = useRef<HTMLElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 768px)')
    const closeOnDesktop = () => { if (desktop.matches) setOpen(false) }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])

  function startCheck(event: MouseEvent) {
    event.preventDefault()
    setOpen(false)
    if (onHome && focusCheckInput()) return
    navigate('/', { state: { focusCheck: true } })
  }

  const checkLink = isShowcase ? '/try' : '/'
  const onCheckClick = isShowcase ? () => setOpen(false) : startCheck

  return (
    <header className="site-header" data-scrolled={scrolled} data-width={width}>
      <div className="site-header-inner">
      <Link to="/" className="wordmark">WorksFr<span className="wordmark-period">.</span></Link>
      <div className="site-header-links sh-desktop">
        {navLabel && <span className="metadata preview-label">{navLabel}</span>}
        <nav aria-label={navLabel ?? 'Site navigation'} ref={desktopNav}>
          {nav.map(({ label, to, icon: Icon, current }) => (
            <Link key={to} to={to} aria-current={current ? 'page' : undefined}>
              {Icon && <Icon aria-hidden="true" />}
              {label}
            </Link>
          ))}
        </nav>
      </div>
      <a
        href={REPO_URL}
        className="site-header-github sh-desktop"
        aria-label="WorksFr on GitHub"
        target="_blank"
        rel="noopener noreferrer"
      >
        <GithubMark />
      </a>
      <Link to={checkLink} className="site-header-cta sh-desktop" onClick={onCheckClick}>Check a tutorial</Link>
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
            if (window.matchMedia('(min-width: 768px)').matches) {
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
            <a
              href={REPO_URL}
              className="mobile-menu-github"
              aria-label="WorksFr on GitHub"
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setOpen(false)}
            >
              <GithubMark />
              GitHub
            </a>
            <Link to={checkLink} className="mobile-menu-cta" onClick={onCheckClick}>Check a tutorial</Link>
          </nav>
        </PopoverContent>
      </Popover>
      </div>
    </header>
  )
}
