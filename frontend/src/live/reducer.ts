import type { components } from '@/api/schema'
import type { ReportLiveState } from '@/components/report/ReportView'

export type CheckEvent = components['schemas']['StoredEvent']['event']
export type LiveCheck = {
  report: components['schemas']['CheckReport']
  live: ReportLiveState
  lastSeq: number
}

export function initialLiveCheck(report: LiveCheck['report']): LiveCheck {
  return { report, live: { runningStepIndex: null, pendingBlocks: [] }, lastSeq: 0 }
}

export function applyEvent(state: LiveCheck, seq: number, event: CheckEvent): LiveCheck {
  if (seq <= state.lastSeq) return state
  const next = { ...state, lastSeq: seq }
  switch (event.type) {
    case 'extracted':
      return {
        ...next,
        report: {
          ...state.report, title: event.title, published: event.published,
          date_source: event.date_source,
        },
        live: { ...state.live, pendingBlocks: event.blocks },
      }
    case 'environment':
      return {
        ...next,
        report: {
          ...state.report, versions: event.versions, failed_packages: event.failed_packages,
          install_pins: event.install_pins,
        },
      }
    case 'step_started':
      return { ...next, live: { ...state.live, runningStepIndex: event.idx } }
    case 'step_final':
      return {
        ...next,
        report: {
          ...state.report,
          steps: [
            ...(state.report.steps ?? []).filter(step => step.idx !== event.step.idx),
            event.step,
          ].sort((a, b) => a.idx - b.idx),
        },
        live: {
          runningStepIndex: state.live.runningStepIndex === event.step.idx
            ? null : state.live.runningStepIndex,
          pendingBlocks: state.live.pendingBlocks.filter(block => block.idx !== event.step.idx),
        },
      }
    case 'done':
      return {
        ...next, report: { ...state.report, status: 'done', score: event.score },
        live: { ...state.live, runningStepIndex: null },
      }
    case 'failed':
      return {
        ...next, report: { ...state.report, status: 'failed', error: event.error },
        live: { ...state.live, runningStepIndex: null },
      }
    case 'step_result':
    case 'repair_attempt':
      return next
  }
}
