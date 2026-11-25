import type { ReactNode } from 'react'
import type { CheckReport, EventBlock } from '@/api/types'
import { LandingFooter } from '@/components/landing/LandingFooter'
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
  notice?: ReactNode
}

export function ReportView({
  report, liveState, sample = false, nav, navLabel, notice,
}: ReportViewProps) {
  const steps = report.steps ?? []
  const blocks = new Map<number, EventBlock>()
  liveState?.pendingBlocks.forEach(block => blocks.set(block.idx, block))
  steps.forEach(step => blocks.set(step.idx, { idx: step.idx, kind: step.kind, code: step.original_code, video_timestamp: null, low_confidence: false }))
  const ordered = [...blocks.values()].sort((a, b) => a.idx - b.idx)
  const finished = new Map(steps.map(step => [step.idx, step]))
  const failureMessage = report.error === 'This page has no code blocks to check.'
    ? 'No code blocks to check.'
    : report.error || 'The check failed without an error message.'

  return (
    <>
    <SiteHeader nav={nav} navLabel={navLabel} width="report" />
    <main className="report-page">
      <ReportHeader report={report} blocks={ordered} sample={sample} />
      {notice}
      {report.status === 'failed' && (
        <section className="failure-card mb-6" role="alert">
          <h2>{failureMessage}</h2>
        </section>
      )}
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
      <ReportEnvironment report={report} />
      <LandingFooter />
    </main>
    </>
  )
}
