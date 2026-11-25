import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { CheckReport, StepReport } from '@/api/types'
import { ReplacementDiff } from './ReplacementDiff'
import { ReportView } from './ReportView'

vi.mock('@/components/SiteHeader', () => ({ SiteHeader: () => null }))
vi.mock('@/components/landing/LandingFooter', () => ({ LandingFooter: () => null }))

const failedReport: CheckReport = {
  id: 'failed-check',
  source_kind: 'url',
  source: 'https://example.com/tutorial',
  status: 'failed',
  created_at: '2026-11-25T09:00:00+00:00',
  date_source: 'unknown',
  error: 'The check stopped because the server restarted. Run it again.',
}

describe('failed check report', () => {
  it('titles an untitled report with its source and puts the error in the failure card', () => {
    const markup = renderToStaticMarkup(<ReportView report={failedReport} />)

    expect(markup).toContain('>https://example.com/tutorial</h1>')
    expect(markup).toMatch(/<section class="failure-card[^"\n]*" role="alert"><h2>The check stopped because the server restarted\. Run it again\.<\/h2>/)
  })

  it.each([
    { source_kind: 'notebook', source: 'tutorial.ipynb' },
    { source_kind: 'paste', source: 'Pasted code' },
  ] satisfies Pick<CheckReport, 'source_kind' | 'source'>[])(
    'uses the source and generic no-code error for $source_kind', (source) => {
      const markup = renderToStaticMarkup(<ReportView report={{
        ...failedReport,
        ...source,
        error: 'This page has no code blocks to check.',
      }} />)

      expect(markup).toContain(`>${source.source}</h1>`)
      expect(markup).toContain('<h2>No code blocks to check.</h2>')
    },
  )
})

it('shows the sandbox review note next to an accepted replacement', () => {
  const step: StepReport = {
    idx: 0, kind: 'python', original_code: 'x = 1', final_code: 'x = 2',
    label: 'broken', verdict: 'fixed', reason: '', explanation: '', output: '', low_confidence: false,
  }
  const markup = renderToStaticMarkup(<ReplacementDiff step={step} />)

  expect(markup).toContain('Replacement that ran')
  expect(markup).toContain('Ran in the sandbox without an error. Its output wasn&#x27;t compared with the tutorial&#x27;s, so read it before you run it.')
})

describe('steps read from a video', () => {
  const base: StepReport = {
    idx: 0, kind: 'python', original_code: 'x = 1', final_code: 'x = 1',
    label: 'works', verdict: null, reason: '', explanation: '', output: '', low_confidence: false,
  }
  const videoReport: CheckReport = {
    id: 'video-check',
    source_kind: 'url',
    source: 'https://www.youtube.com/watch?v=abc123',
    status: 'done',
    created_at: '2026-11-25T09:00:00+00:00',
    date_source: 'unknown',
    steps: [
      { ...base, idx: 0, video_timestamp: 52, low_confidence: true },
      { ...base, idx: 1, original_code: 'y = 2', final_code: 'y = 2' },
    ],
  }

  it('shows the badge, a timestamp link and the misread note for a video step only', () => {
    const markup = renderToStaticMarkup(<ReportView report={videoReport} />)
    const [first, second] = markup.split('<article').slice(1)

    expect(first).toContain('read from video')
    expect(first).toContain('href="https://www.youtube.com/watch?v=abc123&amp;t=52s"')
    expect(first).toContain('rel="noopener noreferrer"')
    expect(first).toContain('>0:52</a>')
    expect(first).toContain('May have been misread from the video.')
    expect(second).not.toContain('read from video')
    expect(second).not.toContain('video-time')
  })

  it('shows the badge for a live step and leaves the timestamp unlinked for a non-YouTube source', () => {
    const markup = renderToStaticMarkup(<ReportView
      report={{ ...videoReport, status: 'running', source: 'https://example.com/clip.mp4', steps: [] }}
      liveState={{
        runningStepIndex: 0,
        pendingBlocks: [{ idx: 0, kind: 'python', code: 'x = 1', video_timestamp: 125, low_confidence: false }],
      }}
    />)

    expect(markup).toContain('read from video')
    expect(markup).toContain('<span class="video-time">2:05</span>')
    expect(markup).not.toContain('misread')
  })
})

describe('tutorial pins', () => {
  const pinReport: CheckReport = {
    id: 'pin-check',
    source_kind: 'url',
    source: 'https://example.com/tutorial',
    status: 'done',
    created_at: '2026-11-25T09:00:00+00:00',
    date_source: 'unknown',
    install_pins: [
      { step_idx: 0, line: 'pip install pandas==0.24.2', package: 'pandas', pinned: '==0.24.2', used: '3.1.1', ecosystem: 'pypi', origin: 'install_line' },
    ],
  }

  it('lists the pinned spec next to the version it was checked with', () => {
    const markup = renderToStaticMarkup(<ReportView report={pinReport} />)

    expect(markup).toContain('Tutorial pins')
    expect(markup).toContain('pandas==0.24.2')
    expect(markup).toContain('Checked with')
    expect(markup).toContain('pandas 3.1.1')
  })

  it('says not installed when the pinned package was not installed', () => {
    const markup = renderToStaticMarkup(<ReportView report={{
      ...pinReport,
      install_pins: [{ ...pinReport.install_pins![0], used: null }],
    }} />)

    expect(markup).toContain('not installed')
  })
})
