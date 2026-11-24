import { describe, expect, it } from 'vitest'
import type { components } from '@/api/schema'
import { applyEvent, initialLiveCheck } from './reducer'

type Report = components['schemas']['CheckReport']
type Event = components['schemas']['StoredEvent']['event']
type Step = components['schemas']['StepReport']

const step = (idx: number): Step => ({
  idx, kind: 'python', original_code: 'x = 1', final_code: 'x = 1',
  label: 'works', reason: '', explanation: '', output: '', low_confidence: false,
})
const report: Report = { id: 'test', source_kind: 'url', source: 'https://example.com', status: 'running', created_at: '2026-11-24T09:00:00+00:00', date_source: 'unknown' }
const score: components['schemas']['Score'] = {
  works: 2, deprecated: 0, broken: 0, cant_verify: 0, fixed: 0,
  fixed_unverified: 0, still_run: 2, checked: 2, ratio: 1,
}
const events: Event[] = [
  { type: 'extracted', blocks: [0, 1].map(idx => ({ idx, kind: 'python', code: 'x = 1', video_timestamp: null, low_confidence: false })), packages: [], title: 'Test', date_source: 'estimate', published: '2020-01-01' },
  { type: 'environment', versions: { pandas: '2.2.3' }, failed_packages: ['missing'], install_pins: [{ step_idx: 0, line: 'pip install pandas==1', package: 'pandas', pinned: '==1', used: '2.2.3', ecosystem: 'pypi', origin: 'install_line' }] },
  { type: 'step_started', idx: 0 },
  { type: 'step_final', step: step(0) },
  { type: 'step_started', idx: 1 },
  { type: 'step_final', step: step(1) },
  { type: 'done', score },
]

describe('applyEvent', () => {
  it('stores the provider and wait deadline using the supplied event time', () => {
    const initial = initialLiveCheck(report)
    expect(initial.wait).toBeNull()
    const state = applyEvent(initial, 1, { type: 'rate_limit_wait', provider: 'local', seconds: 12 }, 5000)
    expect(state.wait).toEqual({ provider: 'local', endsAt: 17000 })
    expect(initial.wait).toBeNull()
    expect(state.report).toBe(initial.report)
    expect(state.live).toBe(initial.live)
    const replaced = applyEvent(state, 2, { type: 'rate_limit_wait', provider: 'backup', seconds: 3 }, 9000)
    expect(replaced.wait).toEqual({ provider: 'backup', endsAt: 12000 })
  })

  it('clears the wait on every other event type', () => {
    const state = applyEvent(initialLiveCheck(report), 1, { type: 'rate_limit_wait', provider: 'local', seconds: 12 }, 5000)
    const clearingEvents: Event[] = [
      ...events,
      { type: 'step_result', idx: 0, label: 'broken', reason: 'old API', output: '' },
      { type: 'repair_attempt', idx: 0, attempt: {
        attempt: 1, patched_code: 'x = 2', explanation: 'candidate', decision: { label: 'works', reason: '' }, verdict: 'fixed',
      } },
      { type: 'failed', error: 'Unavailable' },
    ]
    for (const event of clearingEvents) expect(applyEvent(state, 2, event, 6000).wait).toBeNull()
    expect(state.wait).toEqual({ provider: 'local', endsAt: 17000 })
  })

  it('preserves the wait and its deadline when replay ignores an event', () => {
    const state = applyEvent(initialLiveCheck(report), 2, { type: 'rate_limit_wait', provider: 'local', seconds: 12 }, 5000)
    expect(applyEvent(state, 2, { type: 'rate_limit_wait', provider: 'local', seconds: 12 }, 10000)).toBe(state)
    expect(applyEvent(state, 1, { type: 'step_started', idx: 0 }, 10000)).toBe(state)
    expect(state.wait?.endsAt).toBe(17000)
  })

  it('applies a full run, including metadata, pins, all steps and the score', () => {
    const state = events.reduce((state, event, index) => applyEvent(state, index + 1, event), initialLiveCheck(report))
    expect(state.report.status).toBe('done')
    expect(state.report.steps).toEqual([step(0), step(1)])
    expect(state.report.score).toEqual(score)
    expect(state.report.title).toBe('Test')
    expect(state.report.date_source).toBe('estimate')
    expect(state.report.published).toBe('2020-01-01')
    expect(state.report.versions).toEqual({ pandas: '2.2.3' })
    expect(state.report.failed_packages).toEqual(['missing'])
    expect(state.report.install_pins?.[0].pinned).toBe('==1')
    expect(state.live.pendingBlocks).toEqual([])
    expect(state.live.runningStepIndex).toBeNull()
    expect(state.lastSeq).toBe(7)
  })

  it('ignores equal and lower sequences without mutating the state', () => {
    const state = events.reduce((state, event, index) => applyEvent(state, index + 1, event), initialLiveCheck(report))
    for (const [index, event] of events.entries()) expect(applyEvent(state, index + 1, event)).toBe(state)
  })

  it('keeps finished steps when the check fails mid-run', () => {
    let state = applyEvent(initialLiveCheck(report), 1, { type: 'step_final', step: step(0) })
    state = applyEvent(state, 2, { type: 'step_started', idx: 1 })
    state = applyEvent(state, 3, { type: 'failed', error: 'Install timed out.' })
    expect(state.report.status).toBe('failed')
    expect(state.report.error).toBe('Install timed out.')
    expect(state.report.steps).toEqual([step(0)])
    expect(state.live.runningStepIndex).toBeNull()
  })

  it('keeps every received step and the unrun blocks next to the failure line', () => {
    let state = applyEvent(initialLiveCheck(report), 1, events[0])
    state = applyEvent(state, 2, { type: 'step_final', step: step(0) })
    state = applyEvent(state, 3, { type: 'failed', error: 'This check reached its 15-minute limit at step 2. The steps before it are shown.' })
    expect(state.report.status).toBe('failed')
    expect(state.report.error).toContain('15-minute limit at step 2')
    expect(state.report.steps).toEqual([step(0)])
    expect(state.live.pendingBlocks.map(block => block.idx)).toEqual([1])
  })

  it('replaces snapshot steps by index during replay, preserving order', () => {
    const seeded = initialLiveCheck({ ...report, steps: [step(1), step(0)] })
    const replacement = { ...step(0), output: 'fresh output' }
    const state = applyEvent(seeded, 1, { type: 'step_final', step: replacement })
    expect(state.report.steps).toEqual([replacement, step(1)])
    expect(seeded.report.steps?.[1].output).toBe('')
  })

  it('advances sequence without exposing partial results or repair attempts', () => {
    const state = initialLiveCheck(report)
    const next = applyEvent(state, 1, { type: 'step_result', idx: 0, label: 'broken', reason: 'old API', output: '' })
    const final = applyEvent(next, 2, { type: 'repair_attempt', idx: 0, attempt: {
      attempt: 1, patched_code: 'x = 2', explanation: 'candidate', decision: { label: 'works', reason: '' }, verdict: 'fixed',
    } })
    expect(final.report).toBe(state.report)
    expect(final.live).toBe(state.live)
    expect(final.lastSeq).toBe(2)
  })
})
