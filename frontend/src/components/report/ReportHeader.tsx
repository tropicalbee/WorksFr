import { ArrowUpRight } from 'lucide-react'
import type { CheckReport, EventBlock, Label } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { StatusLabel } from './StatusLabel'

type ReportHeaderProps = {
  report: CheckReport
  blocks: EventBlock[]
  sample?: boolean
}

function isRunnable(kind: EventBlock['kind']): boolean {
  return kind === 'python' || kind === 'repl'
}

export function ReportHeader({ report, blocks, sample = false }: ReportHeaderProps) {
  const steps = report.steps ?? []
  const counts = report.score ?? steps.reduce((counts, step) => {
    if (step.label) counts[step.label]++
    if (step.label === 'broken' && step.verdict === 'fixed') counts.fixed++
    return counts
  }, { works: 0, deprecated: 0, broken: 0, cant_verify: 0, fixed: 0 })
  const sourceUrl = /^https?:\/\//i.test(report.source) ? report.source : null
  const date = report.published
    ? new Intl.DateTimeFormat('en', {
      year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
    }).format(new Date(`${report.published}T00:00:00Z`))
    : null
  const completed = steps.filter(step => isRunnable(step.kind) && step.label).length
  const total = blocks.filter(block => isRunnable(block.kind)).length
  let facts = 'No checkable steps were reported.'
  if (report.status === 'running') facts = `Checked ${completed} of ${total} steps so far`
  else if (report.status === 'failed') facts = 'The check could not finish.'
  else if (report.score) facts = `${report.score.still_run} of ${report.score.checked} checkable steps still run`

  return (
    <header className="report-header">
      <div className="eyebrow">
        Python tutorial
        {sample && <Badge variant="outline">Sample data</Badge>}
      </div>
      <h1>{report.title || 'Untitled tutorial'}</h1>
      <div className="source-row">
        {sourceUrl ? (
          <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
            {report.source}
            <ArrowUpRight aria-hidden="true" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ) : <span>{report.source}</span>}
        {date && (
          <span>Published {date}{report.date_source === 'estimate' ? ' (estimated)' : ''}</span>
        )}
      </div>
      <p className="report-facts" aria-live="polite">{facts}</p>
      {report.status !== 'failed' && (
        <ul className="count-row" aria-label="Step counts">
          {(['works', 'deprecated', 'broken', 'cant_verify'] as Label[]).map(label => (
            <li key={label}>
              <StatusLabel status={label} count={counts[label] ?? 0} />
              {label === 'broken' && (counts.fixed ?? 0) > 0 && (
                <span className="metadata">
                  {counts.fixed} {counts.fixed === 1 ? 'replacement ran' : 'replacements ran'}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </header>
  )
}
