import { formatCaseStudyStats } from './caseStudies'
import type { CaseStudyData } from './caseStudies'

export function CaseStudyStats({ data }: { data: CaseStudyData }) {
  const { tutorialsBroken, stepsFixed, snippetsUsingRemovedApis } = formatCaseStudyStats(
    data.tutorial_totals, data.agent_test,
  )

  return (
    <div className="lp-case-stats">
      <ul aria-label="Case study highlights">
        <li>
          <strong>{tutorialsBroken}</strong>
          <span>hand-picked tutorials broke on today's libraries</span>
        </li>
        <li>
          <strong>{stepsFixed}</strong>
          <span>broken steps got a fix that ran clean</span>
        </li>
        <li>
          <strong>{snippetsUsingRemovedApis}</strong>
          <span>AI-written snippets used code that no longer works</span>
        </li>
      </ul>
      {data.selection && <p className="lp-case-selection">{data.selection}</p>}
    </div>
  )
}
