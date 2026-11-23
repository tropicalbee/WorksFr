import type { CheckReport, EventBlock } from '@/api/types'
import { SiteHeader } from '@/components/SiteHeader'
import type { SiteHeaderProps } from '@/components/SiteHeader'
import { ReportHeader } from './ReportHeader'
import { ReportEnvironment } from './ReportEnvironment'
import { StepEntry } from './StepEntry'

export type ReportLiveState = { runningStepIndex: number | null; pendingBlocks: EventBlock[] }

type ReportViewProps = {
  report: CheckReport
  liveState?: ReportLiveState
  sample?: boolean
  nav?: SiteHeaderProps['nav']
  navLabel?: string
}

export function ReportView({ report, liveState, sample = false, nav, navLabel }: ReportViewProps) {
  const steps = report.steps ?? []
  const blocks = new Map<number, EventBlock>()
  liveState?.pendingBlocks.forEach(block => blocks.set(block.idx, block))
  steps.forEach(step => blocks.set(step.idx, { idx: step.idx, kind: step.kind, code: step.original_code }))
  const ordered = [...blocks.values()].sort((a, b) => a.idx - b.idx)
  const finished = new Map(steps.map(step => [step.idx, step]))

  return (
    <main className="report-page">
      <SiteHeader nav={nav} navLabel={navLabel} />
      <ReportHeader report={report} blocks={ordered} sample={sample} />
      {report.status === 'failed' ? (
        <section className="failure-card">
          <h2>Check interrupted</h2>
          <p>{report.error || 'The check failed without an error message.'}</p>
        </section>
      ) : (
        <div className="step-list">
          {ordered.map(block => (
            <StepEntry
              key={block.idx}
              block={block}
              step={finished.get(block.idx)}
              running={report.status === 'running' && liveState?.runningStepIndex === block.idx}
            />
          ))}
        </div>
      )}
      <ReportEnvironment report={report} />
      <footer>Each result comes from a sandbox run.</footer>
    </main>
  )
}
