import { Link } from 'react-router'
import { formatCaseStudyStats } from './caseStudies'
import type { CaseStudyData } from './caseStudies'

export function CaseStudyStats({ data }: { data: CaseStudyData }) {
  const { count, sites, languages } = data.tutorial_totals
  const { tutorialsBroken, stepsFixed, snippetsUsingRemovedApis } = formatCaseStudyStats(
    data.tutorial_totals, data.agent_test,
  )

  return (
    <div className="lp-case-stats">
      <ul aria-label="Case study highlights">
        <li>
          <strong className="lp-case-broken">{tutorialsBroken}</strong>
          <span>hand-picked tutorials broke on today's libraries</span>
        </li>
        <li>
          <strong className="lp-case-fixed">{stepsFixed}</strong>
          <span>broken steps got a fix that ran clean</span>
        </li>
        <li>
          <strong className="lp-case-broken">{snippetsUsingRemovedApis}</strong>
          <span>AI-written snippets about changed APIs used one that's gone</span>
        </li>
      </ul>
      <p className="lp-muted">
        Real runs on <time dateTime={data.run_date}>{data.run_date}</time>: {count} tutorials from {sites} sources
        in {languages} languages, pandas {data.versions.pandas}.{' '}
        <Link className="lp-case-link" to="#case-studies">See the case studies.</Link>
      </p>
    </div>
  )
}
