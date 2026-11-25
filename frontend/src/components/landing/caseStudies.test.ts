import { describe, expect, it } from 'vitest'
import type { RecentCheck } from '@/api/checkSource'
import { formatCaseStudyStats, formatRecentCheck, selectFeaturedExamples } from './caseStudies'

const check: RecentCheck = {
  id: 'sample',
  title: 'A saved report',
  source: 'https://www.example.org/tutorial',
  source_kind: 'url',
  date_source: 'unknown',
  status: 'done',
  created_at: '2026-11-25T10:00:00Z',
}

describe('formatCaseStudyStats', () => {
  it('formats the three headlines and includes removed APIs hidden by a fallback', () => {
    expect(formatCaseStudyStats(
      { library_breaks: 5, count: 8, fixed_steps: 17 },
      { broke: 2, hidden_removed_api: 3, snippets: 10 },
    )).toEqual({
      tutorialsBroken: '5 of 8',
      stepsFixed: '17',
      snippetsUsingRemovedApis: '5 of 10',
    })
  })

  it('shows zero when there are no breaks or fixes', () => {
    expect(formatCaseStudyStats(
      { library_breaks: 0, count: 3, fixed_steps: 0 },
      { broke: 0, hidden_removed_api: 0, snippets: 4 },
    )).toEqual({
      tutorialsBroken: '0 of 3',
      stepsFixed: '0',
      snippetsUsingRemovedApis: '0 of 4',
    })
  })
})

describe('selectFeaturedExamples', () => {
  it('uses up to four curated reports and leaves an explicitly empty list empty', () => {
    const featured = Array.from({ length: 5 }, (_, index) => ({
      check_id: `featured-${index}`, label: `Curated ${index}`, kind: 'Agent',
    }))
    expect(selectFeaturedExamples(featured, [check])).toEqual(featured.slice(0, 4))
    expect(selectFeaturedExamples([], [check])).toEqual([])
  })

  it('falls back to the first four recent checks with source types', () => {
    const recent: RecentCheck[] = [
      { ...check, id: 'v', source: 'https://youtu.be/sample', showcase_label: ' Video report ' },
      { ...check, id: 'n', source_kind: 'notebook' },
      { ...check, id: 'p', source_kind: 'paste' },
      check,
      { ...check, id: 'extra' },
    ]
    expect(selectFeaturedExamples(undefined, recent)).toEqual([
      { check_id: 'v', label: 'Video report', kind: 'YouTube' },
      { check_id: 'n', label: check.title, kind: 'Notebook' },
      { check_id: 'p', label: check.title, kind: 'Paste' },
      { check_id: 'sample', label: check.title, kind: 'Article' },
    ])
  })
})

describe('formatRecentCheck', () => {
  const now = Date.parse('2026-11-25T12:00:00Z')

  it('formats the report, aligned counts, and unverified repairs separately', () => {
    expect(formatRecentCheck({
      ...check,
      showcase_label: ' Curated title ',
      score: { works: 3, deprecated: 1, broken: 2, fixed: 0, fixed_unverified: 1, cant_verify: 4,
        still_run: 4, checked: 6, ratio: 4 / 6 },
    }, now)).toEqual({
      title: 'Curated title', host: 'example.org', works: '3', broken: '2', fixed: '–',
      unverified: 1, cantVerify: '4', when: '2 h ago', status: '',
    })
  })

  it('keeps unfinished reports readable without invented counts or a malformed time', () => {
    const running = { ...check, status: 'running', title: null, source_kind: 'paste', created_at: '' } as const
    expect(formatRecentCheck(running, now)).toEqual({
      title: 'Untitled tutorial', host: 'pasted code', works: '–', broken: '–', fixed: '–',
      unverified: 0, cantVerify: '–', when: '', status: 'Running',
    })
    expect(formatRecentCheck({ ...running, status: 'failed' }, now).status).toBe('Stopped')
  })
})
