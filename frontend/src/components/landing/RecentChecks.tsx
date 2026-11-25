import { useEffect, useState } from 'react'
import { ArrowRight, X } from 'lucide-react'
import { Link } from 'react-router'
import { loadRecentChecks } from '@/api/checkSource'
import type { RecentCheck } from '@/api/checkSource'
import { formatRecentCheck } from './caseStudies'

type State = { status: 'loading' } | { status: 'error' } | { status: 'ready'; checks: RecentCheck[]; loadedAt: number }

const INITIAL_ROWS = 6

function RecentRow({ check, loadedAt }: { check: RecentCheck; loadedAt: number }) {
  const row = formatRecentCheck(check, loadedAt)
  const counts = [
    { label: 'Works', value: row.works },
    { label: 'Broken', value: row.broken },
    { label: 'Fixed', value: row.fixed, unverified: row.unverified },
    { label: "Can't verify", value: row.cantVerify },
  ]

  return (
    <tr className="lp-result-row lp-recent-row" role="row">
      <th className="lp-result-title" scope="row" role="rowheader">
        <Link className="lp-recent-link" to={`/checks/${encodeURIComponent(check.id)}`}>{row.title}</Link>
        <span className="lp-result-meta">{row.host}{row.status && ` · ${row.status}`}</span>
      </th>
      {counts.map(({ label, value, unverified }) => (
        <td key={label} className="lp-result-count" role="cell">
          <span className="lp-result-mobile-label" aria-hidden="true">{label}</span>
          <span data-zero={value === '–'}>{value}</span>
          {!!unverified && (
            <span className="lp-result-note" title="Replacement verification incomplete">+{unverified} unverified</span>
          )}
        </td>
      ))}
      <td className="lp-recent-when" role="cell">
        {row.when ? <time dateTime={check.created_at}>{row.when}</time> : '–'}
      </td>
    </tr>
  )
}

export function RecentChecks() {
  const [state, setState] = useState<State>({ status: 'loading' })
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    loadRecentChecks(controller.signal)
      .then(checks => {
        if (!controller.signal.aborted) setState({ status: 'ready', checks, loadedAt: Date.now() })
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ status: 'error' })
      })
    return () => controller.abort()
  }, [])

  return (
    <section className="lp-evidence-section lp-recent" id="recent" aria-labelledby="recent-heading">
      <h2 id="recent-heading">Recent checks</h2>
      {state.status === 'loading' && <p className="lp-muted">Loading recent checks…</p>}
      {state.status === 'error' && <p className="lp-muted">Recent checks could not be loaded.</p>}
      {state.status === 'ready' && state.checks.length === 0 && <p className="lp-muted">No checks yet.</p>}
      {state.status === 'ready' && state.checks.length > 0 && (
        <>
          <table id="recent-checks-table" className="lp-results-table lp-recent-table" role="table" aria-labelledby="recent-heading">
            <colgroup>
              <col />
              <col className="lp-recent-count-column" span={3} />
              <col className="lp-recent-unchecked-column" />
              <col className="lp-recent-time-column" />
            </colgroup>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">Tutorial</th>
                <th className="lp-result-align" scope="col" role="columnheader">
                  <span className="lp-mark-dot lp-mark-works" aria-hidden="true" />Works
                </th>
                <th className="lp-result-align" scope="col" role="columnheader">
                  <X className="lp-mark-broken" aria-hidden="true" />Broken
                </th>
                <th className="lp-result-align" scope="col" role="columnheader">
                  <span className="lp-mark-dot lp-mark-fixed" aria-hidden="true" />Fixed
                </th>
                <th className="lp-result-align" scope="col" role="columnheader">
                  <span className="lp-mark-dot lp-mark-unchecked" aria-hidden="true" />Can't verify
                </th>
                <th className="lp-result-align" scope="col" role="columnheader">When</th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {(expanded ? state.checks : state.checks.slice(0, INITIAL_ROWS)).map(check => (
                <RecentRow key={check.id} check={check} loadedAt={state.loadedAt} />
              ))}
            </tbody>
          </table>
          {state.checks.length > INITIAL_ROWS && (
            <button
              className="lp-recent-more"
              type="button"
              aria-expanded={expanded}
              aria-controls="recent-checks-table"
              onClick={() => setExpanded(value => !value)}
            >
              <span>{expanded ? 'Show fewer' : `Show all ${state.checks.length}`}</span>
              {!expanded && <ArrowRight size={14} aria-hidden="true" />}
            </button>
          )}
        </>
      )}
    </section>
  )
}
