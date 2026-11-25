import { describe, expect, it } from 'vitest'
import { formatCaseStudyStats } from './caseStudies'

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
