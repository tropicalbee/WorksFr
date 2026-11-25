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

it('shows the sandbox review note next to a verified replacement', () => {
  const step: StepReport = {
    idx: 0, kind: 'python', original_code: 'x = 1', final_code: 'x = 2',
    label: 'broken', verdict: 'fixed', reason: '', explanation: '', output: '', low_confidence: false,
  }
  const markup = renderToStaticMarkup(<ReplacementDiff step={step} />)

  expect(markup).toContain('Replacement that ran')
  expect(markup).toContain('Ran in the sandbox. Read it before you run it on your machine.')
})
