import { useEffect, useState } from 'react'
import { TriangleAlert, X } from 'lucide-react'
import { Link } from 'react-router'
import { loadRecentChecks } from '@/api/checkSource'
import type { CheckReport } from '@/api/types'

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; checks: CheckReport[] }

function sourceHost(check: CheckReport): string {
  if (check.source_kind === 'notebook') return 'uploaded notebook'
  if (check.source_kind === 'paste') return 'pasted code'
  try {
    return new URL(check.source).hostname.replace(/^www\./, '')
  } catch {
    return check.source
  }
}

function relativeTime(iso: string): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'Yesterday' : `${days} days ago`
}

function Counts({ check }: { check: CheckReport }) {
  const { score } = check
  if (!score) {
    if (check.status === 'running') return <span className="lp-muted">Running</span>
    if (check.status === 'failed') return <span className="lp-muted">Stopped</span>
    return <span />
  }
  return (
    <div className="lp-rcounts">
      {score.broken > 0 && <span className="lp-status lp-s-broken"><X aria-hidden="true" />{score.broken} broken</span>}
      {score.fixed > 0 && <span className="lp-status lp-s-fixed">{score.fixed} fixed</span>}
      {score.fixed_unverified > 0 && (
        <span className="lp-status lp-s-fixed">{score.fixed_unverified} fixed, live call unverified</span>
      )}
      {score.deprecated > 0 && (
        <span className="lp-status lp-s-dep"><TriangleAlert aria-hidden="true" />{score.deprecated} deprecated</span>
      )}
      {score.works > 0 && <span className="lp-status lp-s-works"><span className="lp-dot" />{score.works} work</span>}
      {score.cant_verify > 0 && <span className="lp-status lp-s-unch">{score.cant_verify} can't verify</span>}
    </div>
  )
}

export function RecentChecks() {
  const [state, setState] = useState<State>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    loadRecentChecks(controller.signal)
      .then(checks => setState({ status: 'ready', checks }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ status: 'error' })
      })
    return () => controller.abort()
  }, [])

  return (
    <section className="lp-recent" id="recent" aria-labelledby="recent-heading">
      <h2 id="recent-heading">Recent checks</h2>
      {state.status === 'loading' && <p className="lp-muted">Loading recent checks…</p>}
      {state.status === 'error' && <p className="lp-muted">Recent checks could not be loaded.</p>}
      {state.status === 'ready' && state.checks.length === 0 && <p className="lp-muted">No checks yet.</p>}
      {state.status === 'ready' && state.checks.length > 0 && (
        <ul className="lp-rlist">
          {state.checks.map(check => (
            <li key={check.id}>
              <Link to={`/checks/${check.id}`}>
                <div className="lp-rmain">
                  <span className="lp-rtitle">{check.title || sourceHost(check)}</span>
                  <span className="lp-rhost">{sourceHost(check)}</span>
                </div>
                <Counts check={check} />
                <span className="lp-rtime">{relativeTime(check.created_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
